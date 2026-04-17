# SPDX-License-Identifier: Apache-2.0
"""Read/write helpers for ``~/.tokenpak/credentials.toml``.

Writing TOML is narrow enough here that we hand-serialise rather than
pull in a new dep (``tomli_w`` isn't in pyproject). If the schema ever
gains nested structures, switch to ``tomli_w`` and declare it.

Concurrency: writes go through a temp-file-then-rename dance so a
concurrent reader never sees a half-written file. We don't lock —
writes from multiple `tokenpak creds add` processes would still be
racy, but the write surface isn't expected to run concurrently.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


CONFIG_PATH = Path.home() / ".tokenpak" / "credentials.toml"

# Ids are slugs we embed in TOML table headers + use as CLI args, so
# restrict to a safe character set. Matches what the CLI's add step
# validates before calling us.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def load() -> dict:
    """Return the parsed creds table (``{id: {fields...}}``) or ``{}``."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = tomllib.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}
    creds = data.get("creds") or {}
    return creds if isinstance(creds, dict) else {}


def save(creds: dict) -> Path:
    """Write ``creds`` to the config file with 0600 perms atomically."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    body = _serialise(creds)
    tmp = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
    tmp.write_text(body)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_PATH)
    # os.replace preserves perms of the src, but belt-and-braces:
    os.chmod(CONFIG_PATH, 0o600)
    return CONFIG_PATH


def add(cred_id: str, entry: dict) -> None:
    """Insert or replace one credential entry. Caller validates shape."""
    creds = load()
    creds[cred_id] = entry
    save(creds)


def remove(cred_id: str) -> bool:
    """Return True if ``cred_id`` was present and is now gone."""
    creds = load()
    if cred_id not in creds:
        return False
    del creds[cred_id]
    if creds:
        save(creds)
    else:
        # Leave an empty but valid file rather than deleting — keeps
        # the 0600 perms + user's file-creation decisions intact.
        save({})
    return True


def validate_id(cred_id: str) -> None:
    """Raise ValueError if ``cred_id`` isn't a safe slug."""
    if not _ID_RE.match(cred_id):
        raise ValueError(
            f"invalid id {cred_id!r} — must match [a-z0-9][a-z0-9._-]*"
        )


# ── serialisation ──────────────────────────────────────────────────


def _serialise(creds: dict) -> str:
    """Hand-write TOML for our narrow schema.

    Each entry is a subtable ``[creds.<id>]`` with primitive values.
    Keys are emitted in a stable display order so diffs stay readable.
    """
    lines: list[str] = [
        "# tokenpak credentials — managed by `tokenpak creds add/remove`",
        "# file perms should be 0600; `tokenpak creds doctor` will flag otherwise",
        "",
    ]
    display_order = ("platform", "kind", "key", "token", "scope_hosts", "account_hint", "expires_at")

    for cred_id in sorted(creds):
        body = creds[cred_id] or {}
        lines.append(f"[creds.{_quote_key(cred_id)}]")

        emitted_keys: set[str] = set()
        for key in display_order:
            if key in body:
                lines.append(_toml_assign(key, body[key]))
                emitted_keys.add(key)
        for key in sorted(body):
            if key in emitted_keys:
                continue
            lines.append(_toml_assign(key, body[key]))

        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _quote_key(k: str) -> str:
    """Quote a TOML key only when non-bare chars are present."""
    if re.match(r"^[A-Za-z0-9_-]+$", k):
        return k
    return f'"{k}"'


def _toml_assign(key: str, value) -> str:
    return f"{_quote_key(key)} = {_toml_value(value)}"


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (list, tuple)):
        inner = ", ".join(_toml_value(v) for v in value)
        return f"[{inner}]"
    # Fallback: stringify anything else.
    return _toml_string(str(value))


def _toml_string(s: str) -> str:
    """Emit a basic TOML string, escaping the characters that matter."""
    escaped = (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'
