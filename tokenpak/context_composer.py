"""Context Composer — Prompt Packer.

Takes a BudgetDecision + retrieved chunks and packs the final prompt payload
within the token budget.

Priority order (highest → lowest):
  1. system_prompt   — fixed, always included
  2. session_state   — minimal, always included
  3. user_request    — always included
  4. retrieved_chunks— ranked; dropped first if over budget
  5. recent_turns    — 1-4 turns for continuity
  6. micro_summary   — optional summary of previous phase (300-800 tokens)

Compression policy if over budget:
  1. Drop lowest-ranked chunks first
  2. Replace dropped chunks with 1-3 sentence micro-summary
  3. Keep exact code only where referenced
  4. If still over budget → set escalation_needed=True (once)
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

try:
    import tiktoken  # type: ignore[import]
    _ENCODER = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(text: str) -> int:
        """Count tokens using tiktoken (cl100k_base)."""
        return len(_ENCODER.encode(text))

except Exception:
    def _count_tokens(text: str) -> int:
        """Fast token estimate: characters / 4."""
        return max(1, len(text) // 4)


def _message_tokens(msg: dict[str, Any]) -> int:
    """Estimate token cost of a single chat message dict."""
    content = msg.get("content") or ""
    # 4 overhead tokens per message (role + delimiters)
    return _count_tokens(content) + 4


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    """A single retrieved context chunk with metadata."""
    content: str
    rank: float          # Higher = more relevant
    source: str = ""     # file:line citation
    chunk_id: str = ""


@dataclass
class ComposedContext:
    """Result of context packing."""
    final_prompt_messages: list[dict[str, Any]]
    final_budget: int
    actual_tokens: int
    explain_plan: list[str]            # Why these chunks were chosen
    escalation_needed: bool = False
    dropped_chunks: list[str] = field(default_factory=list)
    summarized_chunks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------

class ContextComposer:
    """Pack prompt components into a budget-constrained context window."""

    # Micro-summary bounds (tokens)
    MICRO_SUMMARY_MIN = 300
    MICRO_SUMMARY_MAX = 800

    def compose(
        self,
        *,
        budget: int,
        system_prompt: str = "",
        session_state: Optional[str] = None,
        user_request: str = "",
        retrieved_chunks: Optional[list[RetrievedChunk]] = None,
        recent_turns: Optional[list[dict[str, Any]]] = None,
        previous_phase_summary: Optional[str] = None,
    ) -> ComposedContext:
        """Pack all components within *budget* tokens.

        Parameters
        ----------
        budget:
            Maximum token count for the final prompt.
        system_prompt:
            Fixed system instructions (always included).
        session_state:
            Minimal session state blob (always included).
        user_request:
            The user's current request (always included).
        retrieved_chunks:
            Ranked retrieval results. Sorted by rank descending; lowest dropped first.
        recent_turns:
            Recent conversation turns (list of {"role": ..., "content": ...}).
        previous_phase_summary:
            Optional summary of the previous reasoning phase (micro-summary).

        Returns
        -------
        ComposedContext
        """
        chunks = sorted(retrieved_chunks or [], key=lambda c: c.rank, reverse=True)
        turns = recent_turns or []
        explain: list[str] = []
        dropped: list[str] = []
        summarized: list[str] = []

        # --- Build mandatory messages (always included) ---
        messages: list[dict[str, Any]] = []

        sys_msg = {"role": "system", "content": system_prompt} if system_prompt else None
        if sys_msg:
            messages.append(sys_msg)
            explain.append("system_prompt: always included")

        if session_state:
            messages.append({"role": "system", "content": f"[session_state]\n{session_state}"})
            explain.append("session_state: always included")

        user_msg = {"role": "user", "content": user_request}
        messages.append(user_msg)
        explain.append("user_request: always included")

        # Tokens used so far
        used = sum(_message_tokens(m) for m in messages)

        # --- Micro-summary (optional, low priority — add tentatively) ---
        micro_msg: Optional[dict[str, Any]] = None
        if previous_phase_summary:
            trimmed = self._trim_to_budget(previous_phase_summary, self.MICRO_SUMMARY_MAX)
            micro_msg = {"role": "system", "content": f"[previous_phase_summary]\n{trimmed}"}

        # --- Recent turns (trim to fit, lowest priority after chunks) ---
        # We'll add turns after chunks so chunks take priority

        # --- Retrieved chunks (ranked, drop lowest first if over budget) ---
        remaining = budget - used

        # Reserve space for micro-summary if provided
        micro_cost = _message_tokens(micro_msg) if micro_msg else 0
        remaining_for_chunks = remaining - micro_cost

        # Reserve space for recent turns (up to 4 turns)
        turn_messages = turns[-4:] if turns else []
        turn_cost = sum(_message_tokens(t) for t in turn_messages)
        remaining_for_chunks -= turn_cost

        included_chunks: list[RetrievedChunk] = []
        for chunk in chunks:
            cost = _count_tokens(chunk.content) + 4
            if remaining_for_chunks >= cost:
                included_chunks.append(chunk)
                remaining_for_chunks -= cost
                explain.append(
                    f"chunk[rank={chunk.rank:.2f}] '{chunk.source or chunk.chunk_id}': included"
                )
            else:
                dropped.append(chunk.chunk_id or chunk.source or chunk.content[:60])
                explain.append(
                    f"chunk[rank={chunk.rank:.2f}] '{chunk.source or chunk.chunk_id}': DROPPED (budget)"
                )

        # Build chunk messages (highest rank first)
        for chunk in included_chunks:
            citation = f" [{chunk.source}]" if chunk.source else ""
            messages.append({
                "role": "system",
                "content": f"[context{citation}]\n{chunk.content}",
            })

        # --- Micro-summary for dropped chunks ---
        if dropped:
            summary_text = self._build_drop_summary(
                [c for c in chunks if (c.chunk_id or c.source or c.content[:60]) in dropped]
            )
            summarized.extend(dropped)
            summary_msg = {
                "role": "system",
                "content": f"[dropped_context_summary]\n{summary_text}",
            }
            summary_cost = _message_tokens(summary_msg)
            # Add summary only if it fits
            current_used = sum(_message_tokens(m) for m in messages)
            current_used += sum(_message_tokens(t) for t in turn_messages)
            current_used += micro_cost
            if current_used + summary_cost <= budget:
                messages.append(summary_msg)
                explain.append(f"micro_summary: {len(dropped)} dropped chunks summarized")

        # --- Add recent turns ---
        if turn_messages:
            messages.extend(turn_messages)
            explain.append(f"recent_turns: {len(turn_messages)} turn(s) included")

        # --- Add micro-summary of previous phase ---
        if micro_msg:
            messages.append(micro_msg)
            explain.append("previous_phase_summary: appended")

        # --- Final token count ---
        actual = sum(_message_tokens(m) for m in messages)

        # --- Escalation check ---
        escalation_needed = actual > budget
        if escalation_needed:
            explain.append(f"ESCALATION: actual={actual} > budget={budget}")

        return ComposedContext(
            final_prompt_messages=messages,
            final_budget=budget,
            actual_tokens=actual,
            explain_plan=explain,
            escalation_needed=escalation_needed,
            dropped_chunks=dropped,
            summarized_chunks=summarized,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _trim_to_budget(self, text: str, max_tokens: int) -> str:
        """Trim text to fit within max_tokens."""
        if _count_tokens(text) <= max_tokens:
            return text
        # Binary-search trim by characters
        lo, hi = 0, len(text)
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if _count_tokens(text[:mid]) <= max_tokens:
                lo = mid
            else:
                hi = mid
        return text[:lo] + "…"

    def _build_drop_summary(self, dropped_chunks: list[RetrievedChunk]) -> str:
        """Build a 1-3 sentence summary of dropped chunks."""
        if not dropped_chunks:
            return ""
        sources = [c.source or c.chunk_id or "unknown" for c in dropped_chunks]
        n = len(dropped_chunks)
        snippet_parts: list[str] = []
        for c in dropped_chunks[:3]:
            first_line = c.content.strip().splitlines()[0][:80] if c.content.strip() else ""
            if first_line:
                snippet_parts.append(f'"{first_line}"')
        snippet = "; ".join(snippet_parts)
        summary = (
            f"{n} context chunk(s) were dropped to fit the budget "
            f"(sources: {', '.join(sources[:5])})."
        )
        if snippet:
            summary += f" Key topics include: {snippet}."
        return textwrap.fill(summary, width=200)
