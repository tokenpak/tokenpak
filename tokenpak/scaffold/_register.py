# SPDX-License-Identifier: Apache-2.0
"""Opt-in in-place patch of credential_injector.py for ``--register``.

Default scaffold behavior is non-destructive (Standard #23 §3
spirit applied to codegen). When the maintainer passes
``--register``, this module:

  1. Reads ``credential_injector.py``.
  2. Finds two anchor points: the existing ``register()`` block and
     the existing ``__all__`` list.
  3. Inserts the new import + register call + __all__ entry.
  4. Atomically writes back (temp-then-rename).

If ANY anchor isn't found, refuses to patch and raises
:class:`RegisterError`. The maintainer falls back to the printed
manual instructions. No regex-based silent failures.
"""

from __future__ import annotations

import re
from pathlib import Path

# Resolved relative to this module: tokenpak/scaffold/_register.py
# is two directories deep from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INJECTOR_PATH = (
    _REPO_ROOT
    / "tokenpak"
    / "services"
    / "routing_service"
    / "credential_injector.py"
)


class RegisterError(Exception):
    """Raised when --register can't safely apply the patch.

    Carries a diagnostic message; CLI surfaces it + falls back to
    the manual-instruction path.
    """


def apply_register_patch(*, vendor_safe: str, class_name: str) -> Path:
    """Apply the import + register() + __all__ patch in-place.

    ``vendor_safe`` is the underscored vendor (e.g. ``mistral``,
    ``azure_openai``). ``class_name`` is the full class name (e.g.
    ``MistralCredentialProvider``). Returns the patched file's
    absolute path on success.

    Raises :class:`RegisterError` when:
      - credential_injector.py isn't where we expect.
      - The expected anchors aren't in the file.
      - The class is already registered (idempotency check).
    """
    if not _INJECTOR_PATH.is_file():
        raise RegisterError(
            f"credential_injector.py not found at {_INJECTOR_PATH}. "
            "Are you in the tokenpak repo root?"
        )

    src = _INJECTOR_PATH.read_text(encoding="utf-8")

    import_line = (
        f"from tokenpak.services.routing_service.extras.{vendor_safe} "
        f"import {class_name}"
    )
    register_line = f"register({class_name}())"

    # Idempotency: if the file already has both the import + register
    # call, nothing to do (safe re-run).
    if import_line in src and register_line in src:
        return _INJECTOR_PATH

    # Anchor 1: the comment block introducing the register() calls.
    register_anchor = "# ── Register built-ins at import"
    if register_anchor not in src:
        raise RegisterError(
            "Could not find the ``# ── Register built-ins at import``"
            " comment in credential_injector.py. The file structure may"
            " have changed; refusing to patch blindly."
        )

    # Anchor 2: the __all__ list. Required so we can keep exports
    # alphabetised (Standard #23 §1 cross-references the existing
    # alphabetical convention in the file).
    all_match = re.search(r"^__all__\s*=\s*\[([^\]]*)\]", src, flags=re.MULTILINE)
    if all_match is None:
        raise RegisterError(
            "Could not find ``__all__`` list in credential_injector.py."
            " Refusing to patch."
        )

    # Insert import line BEFORE the register comment block. We
    # collect all existing ``from ...extras...`` imports + insert
    # alphabetically. If none exist yet, insert just before the
    # register-anchor block.
    src_after_import = _insert_import(src, import_line, register_anchor)

    # Append register() call at the end of the existing register
    # block. Find the LAST consecutive register() line and insert
    # after it.
    src_after_register = _append_register_call(src_after_import, register_line)

    # Add to __all__ in alphabetical order.
    src_final = _add_to_all(src_after_register, class_name)

    # Atomic write.
    tmp = _INJECTOR_PATH.with_suffix(_INJECTOR_PATH.suffix + ".register.tmp")
    tmp.write_text(src_final, encoding="utf-8")
    tmp.rename(_INJECTOR_PATH)
    return _INJECTOR_PATH


def _insert_import(src: str, import_line: str, register_anchor: str) -> str:
    """Insert ``import_line`` before the register-anchor comment.

    Idempotent: if the line is already present, returns ``src``
    unchanged.
    """
    if import_line in src:
        return src
    # Find the register-anchor line and walk backwards to insert
    # before any blank lines preceding it.
    idx = src.index(register_anchor)
    # Step back to start-of-line.
    line_start = src.rfind("\n", 0, idx) + 1
    # Walk back through blank lines so the import sits immediately
    # before the register-anchor comment block (with one blank line
    # separator).
    insert_pos = line_start
    while True:
        prev_nl = src.rfind("\n", 0, insert_pos - 1)
        if prev_nl == -1:
            break
        prev_line = src[prev_nl + 1:insert_pos - 1]
        if prev_line.strip() != "":
            break
        insert_pos = prev_nl + 1
    return src[:insert_pos] + import_line + "\n\n\n" + src[insert_pos:]


def _append_register_call(src: str, register_line: str) -> str:
    """Append ``register_line`` after the last existing register() call."""
    if register_line in src:
        return src
    # Find all ``register(...)`` lines and pick the LAST one.
    matches = list(re.finditer(r"^register\([A-Za-z_][A-Za-z0-9_]*\(\)\)$", src, flags=re.MULTILINE))
    if not matches:
        raise RegisterError(
            "Could not find existing ``register(...)`` lines in "
            "credential_injector.py — nothing to anchor onto."
        )
    last = matches[-1]
    insert_pos = last.end()
    return src[:insert_pos] + "\n" + register_line + src[insert_pos:]


def _add_to_all(src: str, class_name: str) -> str:
    """Insert ``class_name`` into ``__all__`` in alphabetical order."""
    # Match the multiline __all__ block including any embedded entries.
    # Allow whitespace + multi-line; entries are quoted strings.
    pattern = re.compile(
        r"(__all__\s*=\s*\[)([^\]]*)(\])",
        flags=re.MULTILINE,
    )
    m = pattern.search(src)
    if m is None:
        raise RegisterError("Couldn't locate __all__ list (already verified above).")

    inner = m.group(2)
    new_entry = f'"{class_name}"'
    if new_entry in inner:
        return src

    # Split entries (commas + whitespace), preserve indentation by
    # mimicking the existing format: assume each entry is on its
    # own line with consistent indentation.
    entries = [e.strip().rstrip(",") for e in inner.split("\n") if e.strip()]
    entries.append(new_entry)
    # Sort alphabetically (case-insensitive — matches existing convention).
    entries_sorted = sorted(set(entries), key=lambda s: s.lower())
    indent = "    "
    new_inner = (
        "\n"
        + "\n".join(f"{indent}{e}," for e in entries_sorted)
        + "\n"
    )
    return src[:m.start()] + m.group(1) + new_inner + m.group(3) + src[m.end():]
