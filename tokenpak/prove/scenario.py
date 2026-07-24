# SPDX-License-Identifier: Apache-2.0
"""Parse .md scenario files for prove runs.

Scenario format::

    ---
    name: My Test Scenario
    model: claude-sonnet-4-6
    provider: anthropic          # anthropic | openai
    system: You are a helpful coding assistant.
    max_tokens: 4096
    ---

    ## Turn 1: Exploration
    Read the project structure and explain the architecture.

    ## Turn 2: Implementation
    Add input validation to the parse_config function.

    ## Turn 3: Testing
    Write tests for the validation you added.

Scenarios are discovered from:
  1. Built-in: ``tokenpak/prove/scenarios/*.md``
  2. User:     ``~/.tokenpak/prove/scenarios/*.md``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_BUILTIN_DIR = Path(__file__).parent / "scenarios"
_USER_DIR = Path.home() / ".tokenpak" / "prove" / "scenarios"


@dataclass
class Turn:
    """One turn in a multi-turn scenario."""

    number: int
    label: str
    prompt: str


@dataclass
class Scenario:
    """A parsed prove scenario.

    The ``matrix`` field defines which (platform, provider, model)
    combinations to test.  When empty, the runner falls back to the
    legacy two-arm (direct vs proxy) behaviour using ``model`` and
    ``provider`` from the top-level frontmatter.

    Matrix format in frontmatter::

        matrix:
          - name: Sonnet Direct
            platform: api
            provider: anthropic
            model: claude-sonnet-4-6
          - name: Sonnet + TokenPak
            platform: proxy
            provider: anthropic
            model: claude-sonnet-4-6
          - name: GPT-4o Direct
            platform: api
            provider: openai
            model: gpt-4o
    """

    name: str
    model: str = "claude-sonnet-4-6"
    provider: str = "anthropic"
    system: str = "You are a helpful software engineer. Be concise and precise."
    max_tokens: int = 4096
    turns: list[Turn] = field(default_factory=list)
    matrix: list[dict[str, Any]] = field(default_factory=list)
    source_path: str = ""

    @classmethod
    def from_file(cls, path: Path) -> "Scenario":
        """Parse a scenario .md file."""
        text = path.read_text()

        # Split frontmatter from body
        fm, body = _split_frontmatter(text)

        # Parse frontmatter
        meta = yaml.safe_load(fm) if fm else {}
        if not isinstance(meta, dict):
            meta = {}

        scenario = cls(
            name=meta.get("name", path.stem),
            model=meta.get("model", "claude-sonnet-4-6"),
            provider=meta.get("provider", _detect_provider(meta.get("model", ""))),
            system=meta.get("system", cls.system),
            max_tokens=meta.get("max_tokens", 4096),
            matrix=meta.get("matrix", []),
            source_path=str(path),
        )

        # Parse turns from ## headings
        scenario.turns = _parse_turns(body)

        if not scenario.turns:
            raise ValueError(f"No turns found in {path}. Use '## Turn N' headings.")

        return scenario


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split YAML frontmatter from markdown body."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[1].strip(), parts[2].strip()
    return "", text.strip()


def _parse_turns(body: str) -> list[Turn]:
    """Extract turns from ## headings in the markdown body."""
    # Match ## Turn N or ## Turn N: Label or just ## Label
    pattern = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(body))

    turns: list[Turn] = []
    for i, match in enumerate(matches):
        heading = match.group(1).strip()

        # Extract turn number and label
        turn_match = re.match(r"Turn\s+(\d+)(?:\s*:\s*(.+))?", heading, re.IGNORECASE)
        if turn_match:
            number = int(turn_match.group(1))
            label = turn_match.group(2) or f"Turn {number}"
        else:
            number = i + 1
            label = heading

        # Extract prompt text (everything until next ## or end)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        prompt = body[start:end].strip()

        if prompt:
            turns.append(Turn(number=number, label=label, prompt=prompt))

    return turns


def _detect_provider(model: str) -> str:
    """Guess the provider from the model name."""
    model_lower = model.lower()
    if model_lower.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    return "anthropic"


def list_scenarios() -> list[dict[str, Any]]:
    """List all available scenarios with metadata."""
    scenarios: list[dict[str, Any]] = []
    seen: set[str] = set()

    for source, directory in [("user", _USER_DIR), ("built-in", _BUILTIN_DIR)]:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.stem in seen:
                continue
            seen.add(path.stem)
            try:
                s = Scenario.from_file(path)
                scenarios.append(
                    {
                        "id": path.stem,
                        "name": s.name,
                        "model": s.model,
                        "provider": s.provider,
                        "turns": len(s.turns),
                        "source": source,
                        "path": str(path),
                    }
                )
            except Exception as e:
                scenarios.append(
                    {
                        "id": path.stem,
                        "name": f"(error: {e})",
                        "source": source,
                        "path": str(path),
                    }
                )

    return scenarios


def resolve_scenario(name: str) -> Scenario:
    """Find and parse a scenario by name.

    Resolution order: user dir first, then built-in.
    """
    for directory in [_USER_DIR, _BUILTIN_DIR]:
        path = directory / f"{name}.md"
        if path.exists():
            return Scenario.from_file(path)

    available = [s["id"] for s in list_scenarios()]
    raise FileNotFoundError(
        f"Scenario '{name}' not found.\n"
        f"Available: {', '.join(available) if available else '(none)'}\n"
        f"Create one at: {_USER_DIR / f'{name}.md'}"
    )
