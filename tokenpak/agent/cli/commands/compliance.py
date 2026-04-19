"""
tokenpak compliance — Enterprise compliance report generation.

Subcommands:
    tokenpak compliance report <standard>   Generate a compliance report
    tokenpak compliance report soc2
    tokenpak compliance report gdpr
    tokenpak compliance report ccpa
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


def _run_report(standard: str, args: list[str]) -> None:
    if not _enterprise_check():
        _print_upgrade()
        sys.exit(2)

    import argparse
    from datetime import datetime, timezone

    p = argparse.ArgumentParser(prog=f"tokenpak compliance report {standard}")
    p.add_argument("--output", "-o", default=None, help="Save report to file (JSON)")
    p.add_argument(
        "--period-start",
        default=None,
        help="Period start (YYYY-MM-DD). Defaults to 90 days ago.",
    )
    p.add_argument(
        "--period-end",
        default=None,
        help="Period end (YYYY-MM-DD). Defaults to today.",
    )
    p.add_argument("--org", default="", help="Organization name for the report")
    parsed = p.parse_args(args)

    # Use the existing enterprise compliance module (real implementation)
    from tokenpak.enterprise.compliance import ComplianceReporter

    reporter = ComplianceReporter()

    print(f"TOKENPAK  |  Compliance Report — {standard.upper()}")
    print(_SEP)
    print()
    print(f"  Generating {standard.upper()} report...")

    try:
        report = reporter.generate(
            standard,
            since=parsed.period_start,
            until=parsed.period_end,
        )

        print()
        print(report.as_text())

        if parsed.output:
            report.save(parsed.output)
            print(f"\n✓ Report saved to: {parsed.output}")

    except Exception as exc:
        print(f"\n⚠ Report generation error: {exc}", file=sys.stderr)
        print("  Ensure audit logging is configured and data is available.", file=sys.stderr)
        sys.exit(1)


def run(argv: list[str] | None = None) -> None:
    """Entry point for 'tokenpak compliance' command."""
    args = argv if argv is not None else sys.argv[2:]

    if not args:
        print("Usage: tokenpak compliance report <soc2|gdpr|ccpa> [options]")
        print()
        print("  report soc2    Generate SOC 2 compliance report")
        print("  report gdpr    Generate GDPR compliance report")
        print("  report ccpa    Generate CCPA compliance report")
        print()
        if not _enterprise_check():
            print(f"  ⚠  Enterprise license required (current tier: {_tier_name()})")
            print("     https://tokenpak.dev/enterprise")
            print()
        return

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "report":
        if not rest:
            print("Usage: tokenpak compliance report <soc2|gdpr|ccpa>", file=sys.stderr)
            print("  Standards: soc2, gdpr, ccpa", file=sys.stderr)
            sys.exit(1)
        standard = rest[0].lower()
        if standard not in ("soc2", "gdpr", "ccpa"):
            print(f"Unknown standard: {standard!r}. Choose: soc2, gdpr, ccpa", file=sys.stderr)
            sys.exit(1)
        _run_report(standard, rest[1:])
    else:
        print(f"Unknown compliance subcommand: {subcmd}", file=sys.stderr)
        print("Try: tokenpak compliance report <soc2|gdpr|ccpa>", file=sys.stderr)
        sys.exit(1)
