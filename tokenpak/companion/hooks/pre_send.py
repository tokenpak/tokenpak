#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# tokenpak: §5.2-exception — C — The hook imports
# `services.routing_service.classifier` to tag journal rows with the
# canonical RouteClass. Classification is a stateless, side-effect-free
# local lookup (no provider invoked), keeping the companion's local UX
# state aligned with the proxy's taxonomy. Single source of truth.
"""UserPromptSubmit hook — pre-send pipeline for the tokenpak companion.

Runs on every Claude Code prompt submit. Must stay fast (< 100ms) because
it's in the send path. Design: stdlib-only imports, no heavy deps,
best-effort DB writes.

Pipeline (in order):
    1. Parse hook payload from stdin.
    2. Bail if companion disabled via `TOKENPAK_COMPANION_ENABLED=0`.
    3. Estimate token count (transcript file size + prompt text, //4).
    4. Estimate cost from the selected model's input rate.
    5. If a daily budget is configured (`TOKENPAK_COMPANION_BUDGET`),
       check it. Exit 2 (block) if the projected total would exceed it.
    6. Write a journal entry so cross-session analytics see this cycle.
    7. Print a one-line status to stderr so the TUI shows activity.

Exit codes:
    0 — allow send
    2 — block send (companion prints JSON reason to stdout first)

Compatible with both interactive TUI and non-interactive ``--print`` /
cron modes (UserPromptSubmit fires in both since Claude Code 2.1.104+).
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict


# Per-1M-token input rates (USD). Rough — intentional; a precise costing
# pass runs post-wire via the proxy's monitor.db. This keeps the hook
# fast enough to stay in the send loop.
_MODEL_RATES = {
    "opus":   15.00,
    "sonnet":  3.00,
    "haiku":   0.80,
}
_DEFAULT_INPUT_RATE = 3.00  # sonnet default

_COMPANION_DIR = Path(
    os.environ.get("TOKENPAK_COMPANION_DIR",
                   str(Path.home() / ".tokenpak" / "companion"))
)
_JOURNAL_DB = _COMPANION_DIR / "journal.db"
_BUDGET_DB = _COMPANION_DIR / "budget.db"


def _read_input() -> Dict[str, Any]:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}


def _rate_for(model: str) -> float:
    """Pick the per-1M input rate for a model name (best-effort)."""
    m = (model or "").lower()
    for key, rate in _MODEL_RATES.items():
        if key in m:
            return rate
    return _DEFAULT_INPUT_RATE


def _get_daily_total() -> float:
    """Today's accumulated companion-tracked cost (USD)."""
    try:
        if not _BUDGET_DB.exists():
            return 0.0
        conn = sqlite3.connect(str(_BUDGET_DB))
        today = datetime.date.today().isoformat()
        row = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost), 0) "
            "FROM companion_costs WHERE date = ?",
            (today,),
        ).fetchone()
        conn.close()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def _record_daily_cost(cost_est: float) -> None:
    """Append to today's running cost total (best-effort)."""
    if cost_est <= 0:
        return
    try:
        _BUDGET_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_BUDGET_DB))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS companion_costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                timestamp REAL NOT NULL,
                estimated_cost REAL NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO companion_costs (date, timestamp, estimated_cost) "
            "VALUES (?, ?, ?)",
            (datetime.date.today().isoformat(), time.time(), cost_est),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _journal_write_savings(
    session_id: str,
    tokens_avoided: int,
    cost_avoided_usd: float,
    source: str,
) -> None:
    """Record a ``companion_savings`` journal row per the 2026-04-17
    attribution contract.

    ``source`` labels which pre-wire optimization produced the saving:
    ``capsule``, ``vault-enrichment`` (negative tokens; adds context),
    ``prune``, ``dedupe``, etc. Status reads these rows to credit
    tokenpak for pre-wire value independent of any platform cache
    activity.
    """
    try:
        _JOURNAL_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_JOURNAL_DB))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                entry_type TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute(
            "INSERT INTO entries (session_id, timestamp, entry_type, "
            "content, metadata_json) VALUES (?, ?, ?, ?, ?)",
            (
                session_id,
                time.time(),
                "companion_savings",
                source,
                json.dumps({
                    "source": source,
                    "tokens_avoided": int(tokens_avoided),
                    "cost_avoided_usd": round(cost_avoided_usd, 6),
                }),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _load_active_capsule(session_id: str) -> str:
    """Return capsule text for this session, or ``""`` if none.

    Capsules live in ``~/.tokenpak/companion/capsules/<session_id>.md``
    (or a ``active.md`` symlink). Missing = no-op.
    """
    candidates = [
        _COMPANION_DIR / "capsules" / f"{session_id}.md",
        _COMPANION_DIR / "capsules" / "active.md",
    ]
    for path in candidates:
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="replace")
                if text.strip():
                    return text
        except OSError:
            continue
    return ""


def _query_vault_context(prompt: str, budget_chars: int) -> str:
    """Pull relevant vault snippets for this prompt (best-effort).

    Uses ``services.routing_service.classifier``-style lazy import so
    the hook stays fast + fails cleanly when vault isn't initialized.
    Returns concatenated snippet text capped at ``budget_chars``.
    """
    if not prompt or budget_chars <= 0:
        return ""
    try:
        # §5.2-C — pure local helper; we're searching local vault state,
        # not invoking a provider or executing a pipeline.
        from tokenpak.vault.blocks import BlockStore
    except Exception:
        return ""
    try:
        store = BlockStore.default()  # type: ignore[attr-defined]
    except Exception:
        return ""
    try:
        hits = store.search(prompt, top_k=5)
    except Exception:
        return ""
    if not hits:
        return ""
    pieces: list[str] = []
    used = 0
    for h in hits:
        text = getattr(h, "text", None) or getattr(h, "content", None)
        if not text:
            continue
        remaining = budget_chars - used - 20
        if remaining <= 0:
            break
        snippet = text if len(text) <= remaining else text[:remaining]
        pieces.append(snippet)
        used += len(snippet) + 20
    return "\n---\n".join(pieces)


def _should_enrich(route_class: str) -> bool:
    """Claude Code routes are byte-preserve on the wire — the ONLY place
    we can add context is here in the pre-send hook. For non-CC routes
    the proxy's context-enrichment Stage handles it; duplicating here
    would double-inject.
    """
    return route_class.startswith("claude-code-")


def _journal_write(session_id: str, tokens_est: int, cost_est: float,
                   prompt_preview: str, route_class: str) -> None:
    """Record this cycle in the session journal (best-effort).

    ``route_class`` is the value of the canonical
    :class:`tokenpak.core.routing.route_class.RouteClass` enum assigned
    by the shared classifier. Stamping it here keeps every journal row
    attributable to the same taxonomy the proxy uses — no parallel
    notion of "is this Claude Code?".
    """
    try:
        _JOURNAL_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_JOURNAL_DB))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                entry_type TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute(
            "INSERT INTO entries (session_id, timestamp, entry_type, "
            "content, metadata_json) VALUES (?, ?, ?, ?, ?)",
            (
                session_id,
                time.time(),
                "pre_send",
                prompt_preview,
                json.dumps({
                    "tokens_est": tokens_est,
                    "cost_est": round(cost_est, 6),
                    "route_class": route_class,
                }),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _resolve_route_class() -> str:
    """Best-effort: ask the shared classifier for the current route class.

    Falls back to ``"generic"`` if the services layer isn't importable
    (e.g. during a partial install). Never raises — this is
    performance-critical pre-send code.
    """
    try:
        # §5.2-C — pure local helper: the companion hook needs the
        # RouteClass name to stamp its local journal row. We're not
        # doing proxy/services execution here.
        from tokenpak.services.routing_service.classifier import get_classifier

        return get_classifier().classify_from_env().value
    except Exception:
        return "generic"


def run(payload: Dict[str, Any]) -> int:
    """Execute the pre-send pipeline. Returns exit code."""
    # Bail if companion explicitly disabled.
    if os.environ.get("TOKENPAK_COMPANION_ENABLED", "1").lower() in (
        "0", "false", "no", "off"
    ):
        return 0

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        # Older Claude Code builds omit session_id in --print/cron mode;
        # synthesize one so journal rows still group by invocation.
        session_id = f"anon-{os.getpid()}-{int(time.time())}"
    transcript_path = payload.get("transcript_path", "") or ""
    # Both "prompt" (UserPromptSubmit spec) and "message" (Wave-1 legacy)
    # are supported.
    prompt_text = str(
        payload.get("prompt") or payload.get("message") or ""
    )
    prompt_preview = prompt_text[:200]

    # Token estimate: transcript-on-disk size + current prompt text,
    # both divided by 4 (classic char → token approximation). Keeps the
    # hook fast — tiktoken would add ~150ms.
    tokens_est = 0
    if transcript_path:
        try:
            tokens_est += os.path.getsize(transcript_path) // 4
        except OSError:
            pass
    if prompt_text:
        tokens_est += len(prompt_text) // 4

    # Cost estimate using the active model's input rate.
    model = os.environ.get("TOKENPAK_COMPANION_MODEL", "")
    rate = _rate_for(model)
    cost_est = tokens_est * rate / 1_000_000

    # Budget gate — block if projected total would exceed daily budget.
    daily_total = 0.0
    budget_str = os.environ.get("TOKENPAK_COMPANION_BUDGET", "0")
    try:
        budget = float(budget_str)
    except ValueError:
        budget = 0.0
    if budget > 0:
        daily_total = _get_daily_total()
        if daily_total + cost_est > budget:
            msg = (
                f"tokenpak: budget exceeded "
                f"(${daily_total:.2f} + ${cost_est:.4f} projected > "
                f"${budget:.2f} daily)"
            )
            print(msg, file=sys.stderr)
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "decision": "block",
                    "reason": msg,
                }
            }))
            return 2

    # Tag the journal row with the canonical route class so status +
    # dashboards can group companion activity the same way they group
    # proxy-side traffic. Single source of truth.
    route_class = _resolve_route_class()

    # Persist journal + running cost (best-effort, never blocks the hook).
    _journal_write(session_id, tokens_est, cost_est, prompt_preview, route_class)
    _record_daily_cost(cost_est)

    # Active pre-send enrichment — the ONLY place tokenpak can add
    # context on byte-preserve routes (claude-code-*). The proxy's
    # ContextEnrichmentStage is correctly Policy-gated off for
    # byte-preserve, so we must do it here or not at all.
    enriched_parts: list[str] = []
    credit_summary: list[str] = []

    if _should_enrich(route_class) and os.environ.get(
        "TOKENPAK_COMPANION_ENRICH", "1"
    ) != "0":
        # 1. Capsule — if the user has an active session capsule, inject it.
        capsule = _load_active_capsule(session_id)
        if capsule:
            enriched_parts.append(
                "# tokenpak capsule (session memory)\n" + capsule
            )
            avoided = len(capsule) // 4
            saved_usd = avoided * rate / 1_000_000
            _journal_write_savings(session_id, avoided, saved_usd, "capsule")
            credit_summary.append(f"capsule +{avoided} tok")

        # 2. Vault context — query BlockStore with the user's prompt.
        #    Only for prompts that clear the relevance gate (avoid
        #    polluting trivial turns).
        min_query_tokens = int(
            os.environ.get("TOKENPAK_COMPANION_MIN_QUERY_TOKENS", "50")
        )
        if len(prompt_text) // 4 >= min_query_tokens:
            budget_chars = int(
                os.environ.get("TOKENPAK_COMPANION_INJECT_BUDGET", "2000")
            )
            vault_ctx = _query_vault_context(prompt_text, budget_chars)
            if vault_ctx:
                enriched_parts.append(
                    "# tokenpak vault context\n" + vault_ctx
                )
                added = len(vault_ctx) // 4
                # Vault enrichment ADDS tokens — recorded as negative
                # savings so status can render both credit + cost.
                _journal_write_savings(
                    session_id, -added, -(added * rate / 1_000_000),
                    "vault-enrichment",
                )
                credit_summary.append(f"vault +{added} tok")

    # Emit mutation to Claude Code via the additionalContext mechanism
    # (hookSpecificOutput.additionalContext gets prepended to the
    # user's prompt without replacing it).
    if enriched_parts:
        payload_out = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "\n\n".join(enriched_parts),
            }
        }
        print(json.dumps(payload_out))

    # Visible status line — shown by Claude Code's TUI under the input.
    if os.environ.get("TOKENPAK_COMPANION_SHOW_COST", "1") != "0":
        parts = [f"tokenpak: ~{tokens_est:,} tokens"]
        if cost_est > 0:
            parts.append(f"est ${cost_est:.4f}")
        if budget > 0:
            pct = (daily_total / budget) * 100 if budget else 0
            if pct >= 50:
                parts.append(f"budget {pct:.0f}%")
        parts.extend(credit_summary)
        print("  ".join(parts), file=sys.stderr)

    return 0


def main() -> None:
    sys.exit(run(_read_input()))


if __name__ == "__main__":
    main()
