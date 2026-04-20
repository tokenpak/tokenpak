from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
TAG_LINE_RE = re.compile(r"^tags:\s*\[(.*?)\]\s*$", re.MULTILINE)
MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass
class TeacherPackResult:
    version: str
    source_fingerprint: str
    recipe_count: int
    output_dir: Path
    recipes_path: Path
    validation_path: Path


class TeacherPackBuilder:
    def __init__(
        self,
        source_roots: list[Path],
        command_roots: list[Path],
        output_root: Path,
        version: str = "v1",
        default_budget: int = 1600,
    ) -> None:
        self.source_roots = source_roots
        self.command_roots = command_roots
        self.output_root = output_root
        self.version = version
        self.default_budget = default_budget

    def build(self) -> TeacherPackResult:
        markdown_files = self._discover_markdown_files()
        command_intents = self._discover_command_intents()
        source_digest = self._source_fingerprint(markdown_files, command_intents)

        recipes: list[dict[str, Any]] = []
        for intent in sorted(command_intents):
            matched_sources = [
                item
                for item in markdown_files
                if intent in item["text"].lower() or intent in item["tags"]
            ]
            required = [m["path"] for m in matched_sources[:2]]
            optional = [m["path"] for m in matched_sources[2:5]]
            estimated_tokens = sum(m["estimated_tokens"] for m in matched_sources[:5])
            recipes.append(
                {
                    "intent": intent,
                    "required_blocks": required,
                    "optional_blocks": optional,
                    "token_budget": {
                        "target": self.default_budget,
                        "estimated": estimated_tokens,
                        "status": "warning" if estimated_tokens > self.default_budget else "ok",
                    },
                }
            )

        validation = self._validate(recipes, markdown_files)

        output_dir = self.output_root / self.version
        output_dir.mkdir(parents=True, exist_ok=True)
        recipes_path = output_dir / "intent_recipes.json"
        validation_path = output_dir / "validation_report.json"

        recipes_doc = {
            "version": self.version,
            "generated_at": datetime.now(UTC).isoformat(),
            "source_fingerprint": source_digest,
            "recipes": recipes,
        }
        recipes_path.write_text(
            json.dumps(recipes_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        validation_path.write_text(
            json.dumps(
                {"version": self.version, "source_fingerprint": source_digest, **validation},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        return TeacherPackResult(
            version=self.version,
            source_fingerprint=source_digest,
            recipe_count=len(recipes),
            output_dir=output_dir,
            recipes_path=recipes_path,
            validation_path=validation_path,
        )

    def _discover_markdown_files(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for root in self.source_roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.md")):
                try:
                    text = path.read_text(encoding="utf-8")
                except Exception:
                    continue
                tags = sorted({t.strip().lower() for t in self._extract_tags(text) if t.strip()})
                links = sorted(set(self._extract_links(text)))
                estimated_tokens = max(1, int(len(text.split()) * 1.33))
                rows.append(
                    {
                        "path": str(path),
                        "text": text.lower(),
                        "tags": tags,
                        "links": links,
                        "estimated_tokens": estimated_tokens,
                    }
                )
        return rows

    def _discover_command_intents(self) -> list[str]:
        intents: set[str] = set()
        for root in self.command_roots:
            if not root.exists():
                continue
            for py in sorted(root.glob("*.py")):
                if py.name.startswith("__"):
                    continue
                intents.add(py.stem.replace("_", "-"))
        return sorted(intents)

    def _source_fingerprint(self, markdown_files: list[dict[str, Any]], intents: list[str]) -> str:
        h = hashlib.sha256()
        for item in sorted(markdown_files, key=lambda d: d["path"]):
            h.update(item["path"].encode("utf-8"))
            h.update(str(item["estimated_tokens"]).encode("utf-8"))
            h.update("|".join(item["tags"]).encode("utf-8"))
            h.update("|".join(item["links"]).encode("utf-8"))
        h.update("|".join(intents).encode("utf-8"))
        return h.hexdigest()[:16]

    def _extract_tags(self, text: str) -> list[str]:
        tags: list[str] = []
        fm = FRONTMATTER_RE.search(text)
        if fm:
            tag_line = TAG_LINE_RE.search(fm.group(1))
            if tag_line:
                tags.extend([t.strip().strip("\"'") for t in tag_line.group(1).split(",")])
        for line in text.splitlines():
            for token in line.split():
                if token.startswith("#") and len(token) > 1 and token[1:].isalnum():
                    tags.append(token[1:])
        return tags

    def _extract_links(self, text: str) -> list[str]:
        links = [m.group(1).strip() for m in MD_LINK_RE.finditer(text)]
        links.extend(m.group(1).strip() for m in WIKI_LINK_RE.finditer(text))
        return links

    def _validate(
        self, recipes: list[dict[str, Any]], markdown_files: list[dict[str, Any]]
    ) -> dict[str, Any]:
        known_paths = {m["path"] for m in markdown_files}
        missing_sources: list[str] = []
        stale_refs: list[dict[str, str]] = []
        token_budget_warnings: list[dict[str, Any]] = []

        for recipe in recipes:
            if not recipe["required_blocks"]:
                missing_sources.append(recipe["intent"])
            for ref in recipe["required_blocks"] + recipe["optional_blocks"]:
                if ref not in known_paths:
                    stale_refs.append({"intent": recipe["intent"], "ref": ref})
            budget = recipe["token_budget"]
            if budget["estimated"] > budget["target"]:
                token_budget_warnings.append(
                    {
                        "intent": recipe["intent"],
                        "estimated": budget["estimated"],
                        "target": budget["target"],
                    }
                )

        # Also detect markdown links that point to missing local files
        for source in markdown_files:
            for link in source["links"]:
                if link.startswith(("http://", "https://", "#")):
                    continue
                base = Path(source["path"]).parent
                target = (base / link).resolve()
                if not target.exists():
                    stale_refs.append(
                        {"intent": "source-link", "ref": f"{source['path']} -> {link}"}
                    )

        return {
            "summary": {
                "missing_source_count": len(missing_sources),
                "stale_ref_count": len(stale_refs),
                "token_budget_warning_count": len(token_budget_warnings),
            },
            "missing_sources": sorted(set(missing_sources)),
            "stale_refs": stale_refs,
            "token_budget_warnings": token_budget_warnings,
        }


def build_teacher_pack(
    source_roots: list[str],
    command_roots: list[str],
    output_root: str,
    version: str = "v1",
    default_budget: int = 1600,
) -> TeacherPackResult:
    builder = TeacherPackBuilder(
        source_roots=[Path(p).expanduser() for p in source_roots],
        command_roots=[Path(p).expanduser() for p in command_roots],
        output_root=Path(output_root).expanduser(),
        version=version,
        default_budget=default_budget,
    )
    return builder.build()
