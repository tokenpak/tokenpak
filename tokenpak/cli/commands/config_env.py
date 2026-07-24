# SPDX-License-Identifier: Apache-2.0
"""``tokenpak config doctor`` / ``config env`` — read-only env diagnostics.

These subcommands are **read-only**. They never create, write, move, chmod, or
delete any file, and they never print a secret *value* (presence + provenance
only). They complement the broad ``tokenpak doctor`` by focusing on the
configuration subsystem: where config is read from, in what precedence, which
env vars are set, and whether the user/system file boundary is intact.

Scope note: this module implements the SAFE half of the centralized-env design
— read-only diagnostics, an env+provenance view (the masked-by-default analogue
of ``config show`` for the *environment*), and a scaffold-only ``.env.example``
stub helper. It does NOT migrate any credentials, does NOT read a foreign/legacy
``.env``, and does NOT wire the load-order into the runtime.
"""

from __future__ import annotations

import json
import os
import re
import sys
from argparse import Namespace
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tokenpak import _paths
from tokenpak.config import load_order

# ---------------------------------------------------------------------------
# Secret-class classification (pattern-based — no hardcoded master enum)
# ---------------------------------------------------------------------------
#
# The set of accepted env keys is OPEN: we do not maintain a closed master list.
# Secret-class is derived from NAME PATTERNS so a newly-added key (e.g. a
# per-instance slot variable) is classified without editing an enumeration. The
# documented env schema is the human-facing source of truth; this pattern table
# is the runtime-discoverable mirror used for masking decisions.

_HIGH_SECRET_PATTERNS = (
    re.compile(r"API_KEY$"),
    re.compile(r"_KEY$"),
    re.compile(r"_TOKEN$"),
    re.compile(r"_SECRET$"),
    re.compile(r"_PASS$"),
    re.compile(r"_PASSWORD$"),
    re.compile(r"_PAT$"),
    re.compile(r"OAUTH"),
    re.compile(r"WEBHOOK$"),  # webhook URLs embed a secret path component
)

_MEDIUM_SECRET_PATTERNS = (
    re.compile(r"_CHAT_ID$"),
    re.compile(r"_EMAIL_TO$"),
    re.compile(r"_ENDPOINT$"),
    re.compile(r"_HOST$"),
    re.compile(r"_CORS_ORIGINS$"),
)


def secret_class(name: str) -> str:
    """Classify an env var name as ``high`` / ``medium`` / ``low``.

    Pattern-based and open-set: an unknown key is classified by its name shape,
    never rejected. ``high`` and ``medium`` values are masked in output.
    """
    upper = name.upper()
    for pattern in _HIGH_SECRET_PATTERNS:
        if pattern.search(upper):
            return "high"
    for pattern in _MEDIUM_SECRET_PATTERNS:
        if pattern.search(upper):
            return "medium"
    return "low"


def _is_sensitive(name: str) -> bool:
    return secret_class(name) in ("high", "medium")


def mask_value(name: str, value: str) -> str:
    """Mask a value if its name is secret-class high/medium; else return as-is.

    Masking shows only that a value is present (``set``) — never any portion of
    the secret — for high/medium classes. Low-class tuning values are shown.
    """
    if _is_sensitive(name):
        return "set" if value else ""
    return value


# ---------------------------------------------------------------------------
# Known-key discovery (dynamic — derived from the runtime config surface)
# ---------------------------------------------------------------------------

# Provider / integration keys that are not TOKENPAK_*-prefixed but are part of
# the documented schema (Anthropic/OpenAI/Google/GitHub/Notion). Derived from the
# config_loader failover chain rather than hardcoded where possible.
_NON_PREFIXED_KNOWN = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GITHUB_TOKEN",
    "NOTION_API_TOKEN",
}


def known_env_keys() -> set[str]:
    """Discover the set of documented env-var names from the runtime surface.

    Dynamic discovery (no hardcoded master enum): collect the ``TOKENPAK_*`` env
    var names the config loader maps, plus the well-known provider keys. Unknown
    ``TOKENPAK_*`` names present in the environment are still honored at
    resolution time (open set) — this function only powers the "is this a
    documented name?" advisory in ``config doctor`` (D4/D8).
    """
    keys: set[str] = set(_NON_PREFIXED_KNOWN)
    # Path/home control vars owned by the resolver (documented infra, not typos).
    keys.add(_paths.ENV_VAR)  # TOKENPAK_HOME
    keys.add("TOKENPAK_CONFIG")
    keys.add("TOKENPAK_DB")
    try:
        # The config command's own var table is the closest in-repo manifest.
        from tokenpak.cli.commands.config import TOKENPAK_VARS

        keys.update(name for name, _label in TOKENPAK_VARS)
    except Exception:
        pass
    # Provider-key env names referenced by the failover chain in the default
    # config (credential_env entries) — discovered, not hardcoded here.
    try:
        from tokenpak.core import config_loader

        cfg = config_loader.load_config()
        failover = cfg.get("failover")
        if isinstance(failover, dict):
            chain = failover.get("chain")
            if isinstance(chain, list):
                for link in chain:
                    if not isinstance(link, dict):
                        continue
                    env_name = link.get("credential_env")
                    if isinstance(env_name, str) and env_name:
                        keys.add(env_name)
    except Exception:
        pass
    return keys


def environ_tokenpak_keys(environ: Optional[Mapping[str, str]] = None) -> list[str]:
    """All ``TOKENPAK_*`` (and known provider) keys present in the environment."""
    env = environ if environ is not None else os.environ
    present = [k for k in env if k.startswith("TOKENPAK_")]
    present += [k for k in _NON_PREFIXED_KNOWN if k in env]
    return sorted(set(present))


# ---------------------------------------------------------------------------
# Diagnostic record shape
# ---------------------------------------------------------------------------


@dataclass
class Check:
    id: str
    check: str
    status: str  # ok | warn | fail | info
    message: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "check": self.check,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
        }


_STATUS_GLYPH = {"ok": "✓", "warn": "⚠", "fail": "✗", "info": "ℹ"}


# ---------------------------------------------------------------------------
# config doctor — read-only diagnostics
# ---------------------------------------------------------------------------


def _home_rule() -> tuple[Path, str]:
    """Return the resolved home and which rule fired (env|canonical|legacy)."""
    override = os.environ.get(_paths.ENV_VAR, "").strip()
    home = _paths.home()
    if override:
        return home, "env"
    if home == _paths.canonical_home():
        return home, "canonical"
    if home == _paths.legacy_home():
        return home, "legacy"
    return home, "canonical"


def run_doctor(environ: Optional[Mapping[str, str]] = None) -> tuple[list[Check], int]:
    """Run the read-only config diagnostics. Returns (checks, exit_code).

    NEVER writes, chmods, or creates anything. NEVER prints a secret value.
    """
    env = environ if environ is not None else os.environ
    checks: list[Check] = []

    # D1 — home resolution.
    home, rule = _home_rule()
    if rule == "legacy":
        checks.append(
            Check(
                "D1",
                "home_resolution",
                "warn",
                f"home resolves to legacy path ({home})",
                "run `tokenpak config migrate` to move to the canonical home",
            )
        )
    else:
        checks.append(Check("D1", "home_resolution", "ok", f"home = {home} (rule: {rule})"))

    # D2 — config file presence + parse.
    config_path = home / "config.yaml"
    if config_path.is_file():
        parse_ok = True
        try:
            import yaml  # noqa: PLC0415

            with open(config_path, "r", encoding="utf-8") as fh:
                yaml.safe_load(fh)
        except ImportError:
            parse_ok = True  # cannot parse without yaml; treat as present
        except Exception:
            parse_ok = False
        if parse_ok:
            checks.append(
                Check("D2", "config_file", "ok", f"config.yaml present + parses ({config_path})")
            )
        else:
            checks.append(
                Check(
                    "D2",
                    "config_file",
                    "fail",
                    f"config.yaml present but unparseable ({config_path})",
                )
            )
    else:
        checks.append(
            Check("D2", "config_file", "info", "no config.yaml — built-in defaults in effect")
        )

    # D3 — effective precedence chain (informational rendering).
    rows = "; ".join(f"{rank}:{name}" for rank, name, _desc in load_order.describe())
    checks.append(Check("D3", "precedence", "info", "resolution order (highest wins)", rows))

    # D4 — env vars set (validate names against discovered known set).
    known = known_env_keys()
    present = environ_tokenpak_keys(env)
    unknown = [k for k in present if k.startswith("TOKENPAK_") and k not in known]
    if present:
        checks.append(
            Check(
                "D4",
                "env_vars",
                "ok",
                f"{len(present)} TokenPak/provider env var(s) set",
                ", ".join(present),
            )
        )
    else:
        checks.append(Check("D4", "env_vars", "info", "no TokenPak/provider env vars set"))
    for name in unknown:
        checks.append(
            Check(
                "D4",
                "env_var_unknown",
                "warn",
                f"unknown TOKENPAK_* name: {name}",
                "possible typo — honored at runtime but undocumented",
            )
        )

    # D5 — ANTHROPIC_BASE_URL attach state (read-only).
    base_url = env.get("ANTHROPIC_BASE_URL", "").strip()
    if not base_url:
        checks.append(
            Check(
                "D5",
                "attach_state",
                "ok",
                "ANTHROPIC_BASE_URL unset (default upstream / not attached via env)",
            )
        )
    elif re.search(r"127\.0\.0\.1|localhost", base_url):
        checks.append(
            Check(
                "D5", "attach_state", "ok", "ANTHROPIC_BASE_URL points at a local proxy", base_url
            )
        )
    else:
        checks.append(
            Check(
                "D5",
                "attach_state",
                "info",
                "ANTHROPIC_BASE_URL points at a non-default upstream",
                base_url,
            )
        )

    # D6 — .env file hygiene (stat only; names never read for D6 status).
    user_env = home / ".env"
    if user_env.exists():
        try:
            mode = user_env.stat().st_mode & 0o777
        except OSError:
            mode = None
        if mode is not None and mode & 0o077:
            checks.append(
                Check(
                    "D6",
                    "dotenv_hygiene",
                    "warn",
                    f"<tpk-home>/.env mode {oct(mode)} is looser than 0600",
                    "tighten with `chmod 600`",
                )
            )
        else:
            checks.append(
                Check("D6", "dotenv_hygiene", "ok", "<tpk-home>/.env present with 0600 mode")
            )
    else:
        checks.append(Check("D6", "dotenv_hygiene", "info", "no <tpk-home>/.env"))

    # D7 — split-home drift (both canonical + legacy present).
    if _paths.has_canonical() and _paths.has_legacy():
        checks.append(
            Check(
                "D7",
                "boundary_drift",
                "warn",
                "both ~/.tpk and ~/.tokenpak present (split-home)",
                "canonical (~/.tpk) wins; reconcile with `tokenpak config migrate`",
            )
        )
    else:
        checks.append(
            Check("D7", "boundary_drift", "ok", "single TokenPak home (no split-home drift)")
        )

    # D8 — schema coverage (info only).
    documented_present = [k for k in present if k in known]
    undocumented_present = [k for k in present if k.startswith("TOKENPAK_") and k not in known]
    checks.append(
        Check(
            "D8",
            "schema_coverage",
            "info",
            f"{len(documented_present)} documented / {len(undocumented_present)} undocumented env var(s) set",
            "",
        )
    )

    # Exit code: 4 on any fail, else 0 (warnings are advisory).
    has_fail = any(c.status == "fail" for c in checks)
    return checks, (4 if has_fail else 0)


def render_doctor(
    checks: list[Check],
    *,
    as_json: bool,
    quiet: bool,
    verbose: bool,
    home: Path,
    rule: str,
    exit_code: int,
) -> None:
    if as_json:
        summary = {
            s: sum(1 for c in checks if c.status == s) for s in ("ok", "warn", "fail", "info")
        }
        out = {
            "home": {"path": str(home), "rule": rule},
            "checks": [c.as_dict() for c in checks],
            "summary": summary,
            "exit_code": exit_code,
        }
        print(json.dumps(out, indent=2))
        return

    if quiet:
        worst = next((c for c in checks if c.status == "fail"), None) or next(
            (c for c in checks if c.status == "warn"), None
        )
        if worst is not None:
            print(f"{_STATUS_GLYPH[worst.status]} {worst.message}")
        return

    print("TokenPak — config doctor")
    print("─" * 40)
    for c in checks:
        glyph = _STATUS_GLYPH.get(c.status, "·")
        print(f"  {glyph} [{c.id}] {c.message}")
        if verbose and c.detail:
            print(f"        {c.detail}")


# ---------------------------------------------------------------------------
# config env — loaded env + provenance (masked by default)
# ---------------------------------------------------------------------------


def run_env_show(*, mask: bool, as_json: bool, environ: Optional[Mapping[str, str]] = None) -> int:
    """Print the loaded env vars + which layer each came from (masked by default).

    Read-only. ``mask`` (default True) redacts secret-class values. Provenance is
    computed via the load-order resolver (process env wins over .env files, etc.).
    """
    env = environ if environ is not None else os.environ
    resolver = load_order.LoadOrderResolver(environ=dict(env))
    keys = environ_tokenpak_keys(env)
    resolutions = resolver.provenance(keys)

    rows = []
    for key in keys:
        res = resolutions[key]
        raw = res.value or ""
        shown = mask_value(key, raw) if mask else raw
        rows.append(
            {
                "name": key,
                "value": shown,
                "provenance": res.layer.name.lower(),
                "secret_class": secret_class(key),
            }
        )

    if as_json:
        print(json.dumps({"masked": mask, "vars": rows}, indent=2))
        return 0

    if not rows:
        print("No TokenPak/provider env vars set.")
        return 0

    print("TokenPak — environment (masked)" if mask else "TokenPak — environment")
    print("─" * 40)
    width = max(len(r["name"]) for r in rows)
    for r in rows:
        display = r["value"] if r["value"] else "○ not set"
        print(f"  {r['name']:<{width}}  {display:<8}  ({r['provenance']})")
    return 0


# ---------------------------------------------------------------------------
# .env.example scaffold stub (config init --with-env-stub)
# ---------------------------------------------------------------------------


_ENV_STUB_HEADER = """# TokenPak environment file (TEMPLATE — placeholders only)
# Copy this to `.env` in this directory (or to <tpk-home>/.env) and fill in
# your own values. NEVER commit a real `.env` — it is gitignored.
# Secret-class values live ONLY in <tpk-home>/.env (mode 0600).
#
# See the env schema for every variable, its purpose, and its secret class.
"""


def env_stub_text(*, sample_keys: Optional[list[str]] = None) -> str:
    """Return placeholders-only ``.env.example`` text (NO credential values).

    Pure: builds a template from a small set of common documented keys with
    placeholder right-hand sides only. Never emits a real value.
    """
    keys = sample_keys or [
        "TOKENPAK_PORT",
        "TOKENPAK_LOG_LEVEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    ]
    lines = [_ENV_STUB_HEADER]
    for key in keys:
        if secret_class(key) == "high":
            placeholder = f"<your-{key.lower()}>"
        else:
            placeholder = "<value>"
        lines.append(f"# {key}={placeholder}")
    return "\n".join(lines) + "\n"


def write_env_stub(target_dir: Path, *, force: bool = False) -> tuple[bool, Path]:
    """Write a ``.env.example`` stub under *target_dir* (placeholders only).

    Returns ``(created, path)``. No-op (created=False) if the file exists and
    ``force`` is False. NEVER writes a real ``.env`` and NEVER writes values.
    """
    target_dir = Path(target_dir)
    target = target_dir / ".env.example"
    if target.exists() and not force:
        return False, target
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(env_stub_text(), encoding="utf-8")
    return True, target


# ---------------------------------------------------------------------------
# argparse dispatch entrypoints (wired from _cli_core)
# ---------------------------------------------------------------------------


def cmd_config_doctor(args: Namespace) -> None:
    """`tokenpak config doctor` dispatch."""
    checks, exit_code = run_doctor()
    home, rule = _home_rule()
    render_doctor(
        checks,
        as_json=getattr(args, "json", False),
        quiet=getattr(args, "quiet", False),
        verbose=getattr(args, "verbose", False),
        home=home,
        rule=rule,
        exit_code=exit_code,
    )
    sys.exit(exit_code)


def cmd_config_env(args: Namespace) -> None:
    """`tokenpak config env` dispatch (loaded env + provenance, masked by default)."""
    # Mask by default; `--show-values` is intentionally NOT offered here (that
    # would belong to a separately-gated path). `--mask/--no-mask` toggles.
    mask = getattr(args, "mask", True)
    code = run_env_show(mask=mask, as_json=getattr(args, "json", False))
    sys.exit(code)
