"""
tokenpak sla — Enterprise SLA routing management.

Subcommands:
    tokenpak sla status          Show current SLA compliance metrics
    tokenpak sla set             Configure an SLA profile
"""

from __future__ import annotations

import sys

_SEP = "────────────────────────────────────────"


def _enterprise_check() -> bool:
    try:
        from tokenpak.agent.license.activation import is_enterprise

        return is_enterprise()
    except Exception:
        return False


def _tier_name() -> str:
    try:
        from tokenpak.agent.license.activation import get_plan

        return get_plan().tier.value.upper()
    except Exception:
        return "OSS"


def _print_upgrade() -> None:
    print("TOKENPAK  |  Enterprise Feature")
    print(_SEP[:32])
    print()
    print("This feature requires an Enterprise license.")
    print(f"Current tier: {_tier_name()}")
    print()
    print("Learn more: https://tokenpak.dev/enterprise")
    print()


def _run_status(profile: str | None = None) -> None:
    if not _enterprise_check():
        _print_upgrade()
        sys.exit(2)

    from tokenpak.enterprise.sla import SLARouter

    router = SLARouter()
    status = router.status(profile)

    print("TOKENPAK  |  SLA Status")
    print(_SEP)
    print()
    print(f"  Profile     : {status.profile}")
    print(f"  Tier        : {status.tier.value.upper()}")
    print(f"  Compliance  : {status.compliance_pct:.1f}%")
    print()
    print("  Latency (rolling):")
    print(f"    p50 : {status.p50_latency_ms:.0f} ms")
    print(f"    p95 : {status.p95_latency_ms:.0f} ms")
    print(f"    p99 : {status.p99_latency_ms:.0f} ms")
    print()
    print(f"  Availability : {status.availability_pct:.2f}%")
    print(f"  Error rate   : {status.error_rate_pct:.2f}%")

    if status.incidents:
        print()
        print(f"  Incidents ({len(status.incidents)}):")
        for inc in status.incidents[:5]:
            print(f"    • {inc.get('ts', '?')} — {inc.get('description', '?')}")

    print()


def _run_set(args: list[str]) -> None:
    if not _enterprise_check():
        _print_upgrade()
        sys.exit(2)

    import argparse

    p = argparse.ArgumentParser(prog="tokenpak sla set")
    p.add_argument("--name", required=True, help="Profile name")
    p.add_argument(
        "--tier",
        choices=["standard", "enhanced", "guaranteed"],
        default="enhanced",
    )
    p.add_argument("--max-latency-ms", type=int, default=5000, help="p95 target in ms")
    p.add_argument("--min-availability", type=float, default=99.0, help="Uptime %% target")
    p.add_argument("--max-error-rate", type=float, default=1.0, help="Max error rate %%")
    p.add_argument("--fallback", action="append", default=[], metavar="MODEL")
    p.add_argument("--priority-provider", action="append", default=[], metavar="PROVIDER")
    p.add_argument("--description", default="")

    parsed = p.parse_args(args)

    from tokenpak.enterprise.sla import SLAProfile, SLARouter, SLATier

    profile = SLAProfile(
        name=parsed.name,
        tier=SLATier(parsed.tier),
        max_latency_ms=parsed.max_latency_ms,
        min_availability_pct=parsed.min_availability,
        max_error_rate_pct=parsed.max_error_rate,
        fallback_models=parsed.fallback,
        priority_providers=parsed.priority_provider,
        description=parsed.description,
    )

    router = SLARouter()
    router.set_profile(profile)
    print(
        f"✓ SLA profile '{parsed.name}' saved (tier={parsed.tier}, p95≤{parsed.max_latency_ms}ms)"
    )


def run(argv: list[str] | None = None) -> None:
    """Entry point for 'tokenpak sla' command."""
    args = argv if argv is not None else sys.argv[2:]

    if not args:
        print("Usage: tokenpak sla <status|set> [options]")
        print()
        print("  status [--profile NAME]   Show SLA compliance metrics")
        print("  set                       Configure an SLA profile")
        print()
        return

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "status":
        import argparse

        p = argparse.ArgumentParser(prog="tokenpak sla status", add_help=False)
        p.add_argument("--profile", default=None)
        parsed, _ = p.parse_known_args(rest)
        _run_status(parsed.profile)
    elif subcmd == "set":
        _run_set(rest)
    else:
        print(f"Unknown sla subcommand: {subcmd}", file=sys.stderr)
        print("Try: tokenpak sla status|set", file=sys.stderr)
        sys.exit(1)
