# SPDX-License-Identifier: Apache-2.0
"""User prompt templates — local CRUD with {{variable}} substitution.

Templates are stored in ~/.tokenpak/templates/<name>.json
Each template is a JSON file with keys: name, content, created_at, updated_at.

Variable syntax: {{variable_name}}

Usage (module):
    from tokenpak.cli.user_templates import add, list_templates, show, remove, use

CLI:
    tokenpak template list
    tokenpak template add my-template --content "Summarise {{topic}} in 3 bullets"
    tokenpak template show my-template
    tokenpak template use my-template --var topic="AI safety"
    tokenpak template remove my-template
"""

from __future__ import annotations

__all__ = (
    "add",
    "cmd_template_add",
    "cmd_template_list",
    "cmd_template_remove",
    "cmd_template_show",
    "cmd_template_use",
    "list_templates",
    "remove",
    "show",
    "use",
    "variables_in",
)


import json
import re
import sys
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TypedDict


class Template(TypedDict):
    """Validated on-disk prompt-template record."""

    name: str
    content: str
    created_at: str
    updated_at: str


def _resolve_templates_dir() -> Path:
    """Canonical ~/.tpk/templates with legacy ~/.tokenpak/templates fallback."""
    from tokenpak import _paths

    return _paths.under("templates")


TEMPLATES_DIR = _resolve_templates_dir()
VARIABLE_RE = re.compile(r"\{\{(\w+)\}\}")


# ── Storage helpers ──────────────────────────────────────────────────────────


def _templates_dir() -> Path:
    d = TEMPLATES_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _template_path(name: str) -> Path:
    safe = re.sub(r"[^\w\-]", "_", name)
    return _templates_dir() / f"{safe}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Core CRUD ────────────────────────────────────────────────────────────────


def _decode_template(raw: object) -> Optional[Template]:
    """Validate the JSON boundary before exposing a template record."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    content = raw.get("content")
    created_at = raw.get("created_at")
    updated_at = raw.get("updated_at")
    if (
        not isinstance(name, str)
        or not isinstance(content, str)
        or not isinstance(created_at, str)
        or not isinstance(updated_at, str)
    ):
        return None
    return Template(
        name=name,
        content=content,
        created_at=created_at,
        updated_at=updated_at,
    )


def list_templates() -> list[Template]:
    """Return all templates sorted by name."""
    templates = []
    for p in sorted(_templates_dir().glob("*.json")):
        try:
            data = _decode_template(json.loads(p.read_text()))
            if data is not None:
                templates.append(data)
        except Exception:
            pass
    return templates


def add(name: str, content: str) -> Template:
    """Create or overwrite a template. Returns the saved template dict."""
    path = _template_path(name)
    now = _now()
    if path.exists():
        existing = _decode_template(json.loads(path.read_text()))
        if existing is None:
            existing = Template(name=name, content="", created_at=now, updated_at=now)
        template = Template(
            name=existing["name"],
            content=content,
            created_at=existing["created_at"],
            updated_at=now,
        )
    else:
        template = Template(name=name, content=content, created_at=now, updated_at=now)
    path.write_text(json.dumps(template, indent=2))
    return template


def show(name: str) -> Optional[Template]:
    """Return a template dict by name, or None if not found."""
    path = _template_path(name)
    if not path.exists():
        return None
    try:
        return _decode_template(json.loads(path.read_text()))
    except Exception:
        return None


def remove(name: str) -> bool:
    """Delete a template. Returns True if deleted, False if not found."""
    path = _template_path(name)
    if path.exists():
        path.unlink()
        return True
    return False


def use(name: str, variables: Optional[dict[str, str]] = None) -> Optional[str]:
    """Expand a template with variables. Returns rendered string, or None if not found."""
    template = show(name)
    if template is None:
        return None
    content = template["content"]
    variables = variables or {}
    for k, v in variables.items():
        content = content.replace(f"{{{{{k}}}}}", v)
    return content


def variables_in(name: str) -> Optional[list[str]]:
    """Return list of {{variable}} names in a template, or None if not found."""
    template = show(name)
    if template is None:
        return None
    return sorted(set(VARIABLE_RE.findall(template["content"])))


# ── CLI helpers (argparse-based, wired into cli.py) ──────────────────────────


def cmd_template_list(args: Namespace) -> None:
    templates = list_templates()
    if not templates:
        print("No templates saved. Add one with: tokenpak template add <name> --content '...'")
        return
    print(f"{'NAME':<30}  VARIABLES")
    print("─" * 60)
    for t in templates:
        vars_found = sorted(set(VARIABLE_RE.findall(t["content"])))
        vars_str = ", ".join(f"{{{{{v}}}}}" for v in vars_found) if vars_found else "—"
        print(f"  {t['name']:<28}  {vars_str}")


def cmd_template_add(args: Namespace) -> None:
    name = args.name
    content = getattr(args, "content", None)

    if not content:
        # Interactive: read from stdin
        print(f"Enter template content for '{name}' (Ctrl-D when done):")
        try:
            content = sys.stdin.read().strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return

    if not content:
        print("❌ No content provided.")
        return

    add(name, content)
    vars_found = sorted(set(VARIABLE_RE.findall(content)))
    print(f"✅ Template '{name}' saved.")
    if vars_found:
        print(f"   Variables: {', '.join(f'{{{{{v}}}}}' for v in vars_found)}")
    print(
        f"   Use with: tokenpak template use {name}"
        + ("".join(f" --var {v}=<value>" for v in vars_found))
    )


def cmd_template_show(args: Namespace) -> None:
    template = show(args.name)
    if template is None:
        print(f"❌ Template '{args.name}' not found.")
        return
    print(f"Name:       {template['name']}")
    print(f"Created:    {template.get('created_at', '—')}")
    print(f"Updated:    {template.get('updated_at', '—')}")
    vars_found = sorted(set(VARIABLE_RE.findall(template["content"])))
    if vars_found:
        print(f"Variables:  {', '.join(vars_found)}")
    print()
    print(template["content"])


def cmd_template_remove(args: Namespace) -> None:
    deleted = remove(args.name)
    if deleted:
        print(f"✅ Template '{args.name}' removed.")
    else:
        print(f"❌ Template '{args.name}' not found.")


def cmd_template_use(args: Namespace) -> None:
    variables: dict[str, str] = {}
    for item in getattr(args, "var", []) or []:
        if "=" in item:
            k, v = item.split("=", 1)
            variables[k.strip()] = v.strip()
        else:
            print(f"⚠️  Ignoring malformed --var '{item}' (expected key=value)")

    result = use(args.name, variables)
    if result is None:
        print(f"❌ Template '{args.name}' not found.")
        return
    print(result)
