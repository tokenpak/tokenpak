# SPDX-License-Identifier: Apache-2.0
"""``tokenpak creds`` subcommands.

MVP surface: ``list`` + ``doctor``. ``add/remove/test/route`` ship in
the next slice once the discovery + hazard layer has caught the
ownership bugs.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from . import store
from .doctor import Issue
from .doctor import run as doctor_run
from .model import KIND_OAUTH, Credential
from .providers import discover_all


def _age_or_expiry(cred: Credential, now: int) -> str:
    if cred.kind != KIND_OAUTH or cred.expires_at is None:
        return "-"
    delta = cred.expires_at - now
    if delta < 0:
        days = abs(delta) // 86400
        if days >= 1:
            return f"EXPIRED {days}d ago"
        return "EXPIRED"
    if delta < 3600:
        return f"expires {delta // 60}m"
    if delta < 86400:
        return f"expires {delta // 3600}h"
    return f"expires {delta // 86400}d"


def _format_expiry_display(cred: Credential) -> str:
    if cred.expires_at is None:
        return "-"
    try:
        return datetime.fromtimestamp(cred.expires_at, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "-"


def cmd_list(args) -> int:
    """Render discovered credentials as a padded table."""
    creds = discover_all()
    if not creds:
        print("no credentials found", file=sys.stderr)
        return 0

    now = int(time.time())
    rows: list[list[str]] = [
        ["ID", "PLATFORM", "KIND", "REFRESH", "EXPIRES", "STATUS", "SOURCE"]
    ]
    for c in creds:
        rows.append(
            [
                c.id,
                c.platform,
                c.kind,
                c.refresh_owner,
                _format_expiry_display(c),
                _age_or_expiry(c, now),
                c.source,
            ]
        )

    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for i, row in enumerate(rows):
        line = "  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row))
        print(line.rstrip())
        if i == 0:
            print("  ".join("-" * widths[j] for j in range(len(widths))))
    return 0


def cmd_doctor(args) -> int:
    """Run hazard checks and print a grouped report. Non-zero on any error."""
    creds = discover_all()
    issues = doctor_run(creds)

    print(f"discovered {len(creds)} credentials from {len({c.provider for c in creds})} providers")

    if not issues:
        print("no issues")
        return 0

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warn"]

    def _print(label: str, items: list[Issue]) -> None:
        if not items:
            return
        print(f"\n{label} ({len(items)}):")
        width = max(len(i.subject) for i in items)
        for i in items:
            print(f"  [{i.severity.upper():5}] {i.subject.ljust(width)}  {i.detail}")

    _print("errors", errors)
    _print("warnings", warnings)
    return 1 if errors else 0


def cmd_route(args: list[str]) -> int:
    """Dry-run the router: show which credential would serve a request.

    Takes one positional: the destination host. Optional flags model
    the caller identity and an explicit tag. No network calls, no
    side effects.
    """
    from .router import AmbiguousRoute, NoRoute, RouteContext, select

    if not args or args[0].startswith("-"):
        print(
            "usage: tokenpak creds route <dest-host> [--caller X] [--tag ID]",
            file=sys.stderr,
        )
        return 2

    dest = args[0]
    kv = _parse_kv_flags(args[1:], known=("--caller", "--tag"))
    if kv is None:
        print("tokenpak creds route: bad flags (try --caller X --tag Y)", file=sys.stderr)
        return 2

    ctx = RouteContext(
        destination_host=dest,
        caller_identity=kv.get("--caller"),
        explicit_tag=kv.get("--tag"),
    )

    try:
        decision = select(ctx)
    except AmbiguousRoute as exc:
        print(f"[AMBIGUOUS] {exc}", file=sys.stderr)
        return 1
    except NoRoute as exc:
        print(f"[NO ROUTE] {exc}", file=sys.stderr)
        return 1

    cred = decision.credential
    print(
        f"[{decision.layer.upper()}] {cred.id}  "
        f"platform={cred.platform}  kind={cred.kind}  "
        f"refresh={cred.refresh_owner}  source={cred.source}"
    )
    print(f"  reason: {decision.reason}")
    return 0


def cmd_test(args: list[str]) -> int:
    """Live-verify one credential by making a cheap platform-specific call."""
    from .tester import test

    if not args or args[0].startswith("-"):
        print("usage: tokenpak creds test <id>", file=sys.stderr)
        return 2
    cred_id = args[0]

    creds = [c for c in discover_all() if c.id == cred_id]
    if not creds:
        print(f"tokenpak creds test: no credential named {cred_id!r}", file=sys.stderr)
        return 1
    cred = creds[0]

    result = test(cred)
    status_tag = "OK" if result.ok else ("SKIP" if not result.supported else "FAIL")
    print(f"[{status_tag}] {cred.id} ({cred.platform})  {result.detail}")
    if not result.supported:
        return 0  # "no probe" isn't an error
    return 0 if result.ok else 1


def cmd_add(args: list[str]) -> int:
    """Add or replace a BYOK credential in ~/.tokenpak/credentials.toml.

    Flags fill in non-interactively; whatever's missing is prompted on
    a TTY. On non-TTY stdin with missing flags we fail instead of
    hanging.
    """
    parsed = _parse_kv_flags(
        args,
        known=("--id", "--platform", "--kind", "--key", "--token", "--scope", "--account"),
    )
    if parsed is None:
        print(
            "usage: tokenpak creds add --id X --platform Y --kind (api_key|bearer) "
            "--key Z [--scope host,host] [--account label]",
            file=sys.stderr,
        )
        return 2

    cred_id = parsed.get("--id") or _prompt("id", required=True)
    if not cred_id:
        return 2
    try:
        store.validate_id(cred_id)
    except ValueError as exc:
        print(f"tokenpak creds add: {exc}", file=sys.stderr)
        return 2

    # Refuse to shadow a credential another provider already owns; the
    # BYOK file is for user-pasted secrets only.
    conflicts = [c for c in discover_all() if c.id == cred_id and c.provider != "user-config"]
    if conflicts:
        other = conflicts[0]
        print(
            f"tokenpak creds add: id {cred_id!r} already exists via "
            f"provider {other.provider} ({other.source}) — pick a different id",
            file=sys.stderr,
        )
        return 2

    platform = (parsed.get("--platform") or _prompt("platform (openai|anthropic|google|xai|...)", required=True) or "").lower()
    if not platform:
        return 2

    kind = (parsed.get("--kind") or _prompt("kind (api_key|bearer)", default="api_key")).lower()
    if kind not in ("api_key", "bearer"):
        print(f"tokenpak creds add: kind must be api_key or bearer, got {kind!r}", file=sys.stderr)
        return 2

    secret_flag = "--key" if kind == "api_key" else "--token"
    secret = parsed.get(secret_flag) or parsed.get("--key") or parsed.get("--token")
    if not secret:
        secret = _prompt_secret(f"{secret_flag.lstrip('-')} (input hidden)", required=True)
    if not secret:
        return 2

    scope_raw = parsed.get("--scope") or _prompt("scope hosts (comma-separated, optional)", default="")
    scope_hosts = [h.strip() for h in scope_raw.split(",") if h.strip()]

    account_hint = parsed.get("--account")

    entry: dict = {"platform": platform, "kind": kind}
    if kind == "api_key":
        entry["key"] = secret
    else:
        entry["token"] = secret
    if scope_hosts:
        entry["scope_hosts"] = scope_hosts
    if account_hint:
        entry["account_hint"] = account_hint

    store.add(cred_id, entry)
    print(f"added {cred_id} ({platform}, {kind}) to {store.CONFIG_PATH}")
    return 0


def cmd_remove(args: list[str]) -> int:
    """Remove a credential from credentials.toml. Does not touch other providers."""
    if not args or args[0].startswith("-"):
        print("usage: tokenpak creds remove <id>", file=sys.stderr)
        return 2
    cred_id = args[0]

    owned_here = {
        c.id for c in discover_all() if c.provider == "user-config"
    }
    if cred_id not in owned_here:
        # Check whether another provider owns it so we can point the user elsewhere.
        other = next((c for c in discover_all() if c.id == cred_id), None)
        if other:
            print(
                f"tokenpak creds remove: {cred_id} is owned by provider "
                f"{other.provider} ({other.source}) — remove it there instead",
                file=sys.stderr,
            )
        else:
            print(f"tokenpak creds remove: no credential named {cred_id!r}", file=sys.stderr)
        return 1

    if store.remove(cred_id):
        print(f"removed {cred_id} from {store.CONFIG_PATH}")
        return 0
    print(f"tokenpak creds remove: {cred_id} not in {store.CONFIG_PATH}", file=sys.stderr)
    return 1


def _parse_kv_flags(args: list[str], known: tuple[str, ...]) -> "dict[str, str] | None":
    """Tiny argparse-free flag parser. Returns None on parse error."""
    out: dict[str, str] = {}
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            i += 1
            continue
        if tok.startswith("--"):
            if "=" in tok:
                name, _, val = tok.partition("=")
            elif i + 1 < len(args):
                name, val = tok, args[i + 1]
                i += 1
            else:
                return None
            if name not in known:
                return None
            out[name] = val
            i += 1
            continue
        return None
    return out


def _prompt(label: str, default: str = "", required: bool = False) -> str:
    if not sys.stdin.isatty():
        if required:
            print(f"tokenpak creds add: missing required field {label!r}", file=sys.stderr)
        return default
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"  {label}{suffix}: ").strip()
    except EOFError:
        return default
    return raw or default


def _prompt_secret(label: str, required: bool = False) -> str:
    if not sys.stdin.isatty():
        if required:
            print(f"tokenpak creds add: missing required secret {label!r}", file=sys.stderr)
        return ""
    import getpass
    try:
        return getpass.getpass(f"  {label}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    sub = args[0] if args else "list"
    rest = args[1:]

    if sub in ("list", "ls"):
        return cmd_list(rest)
    if sub == "doctor":
        return cmd_doctor(rest)
    if sub == "add":
        return cmd_add(rest)
    if sub in ("remove", "rm"):
        return cmd_remove(rest)
    if sub == "test":
        return cmd_test(rest)
    if sub == "route":
        return cmd_route(rest)

    print(f"tokenpak creds: unknown subcommand {sub!r}", file=sys.stderr)
    print("usage: tokenpak creds [list|doctor|add|remove|test|route]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
