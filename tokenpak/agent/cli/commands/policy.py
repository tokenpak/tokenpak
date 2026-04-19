"""
tokenpak policy — Enterprise policy management.

Subcommands:
    tokenpak policy show              List all policies
    tokenpak policy set               Create or update a policy (interactive)
    tokenpak policy enforce <model>   Test enforcement for a model + context
"""

from __future__ import annotations

import sys

_SEP = "────────────────────────────────────────"


def _enterprise_check() -> bool:
    """Return True if Enterprise license is active."""
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


def _run_show() -> None:
    if not _enterprise_check():
        _print_upgrade()
        sys.exit(2)

    from tokenpak.enterprise.policy import PolicyEngine

    engine = PolicyEngine()
    policies = engine.list_policies()

    print("TOKENPAK  |  Policy Engine")
    print(_SEP)
    print()

    if not policies:
        print("  No policies configured.")
        print()
        print("  Use 'tokenpak policy set' to create your first policy.")
    else:
        for p in policies:
            enabled = "✓" if p.enabled else "✗"
            print(f"  [{enabled}] {p.id}  •  {p.name}")
            print(f"        Action : {p.action.value.upper()}")
            print(f"        Scope  : {p.scope.value}")
            if p.description:
                print(f"        Desc   : {p.description}")
            print()

    print()


def _run_set(args: list[str]) -> None:
    if not _enterprise_check():
        _print_upgrade()
        sys.exit(2)

    import argparse

    p = argparse.ArgumentParser(prog="tokenpak policy set")
    p.add_argument("--id", required=True, help="Policy ID")
    p.add_argument("--name", required=True, help="Human-readable name")
    p.add_argument(
        "--action",
        choices=["allow", "deny", "warn", "audit", "reroute"],
        required=True,
    )
    p.add_argument(
        "--scope",
        choices=["model", "provider", "user", "team", "global"],
        default="model",
    )
    p.add_argument("--description", default="")
    p.add_argument("--priority", type=int, default=100)
    p.add_argument("--disable", action="store_true")

    parsed = p.parse_args(args)

    from tokenpak.enterprise.policy import Policy, PolicyAction, PolicyEngine, PolicyScope

    policy = Policy(
        id=parsed.id,
        name=parsed.name,
        description=parsed.description,
        scope=PolicyScope(parsed.scope),
        action=PolicyAction(parsed.action),
        priority=parsed.priority,
        enabled=not parsed.disable,
    )

    engine = PolicyEngine()
    engine.set_policy(policy)
    print(f"✓ Policy '{parsed.id}' saved ({parsed.action.upper()}, scope={parsed.scope})")


def _run_enforce(model: str, extra_args: list[str]) -> None:
    if not _enterprise_check():
        _print_upgrade()
        sys.exit(2)

    from tokenpak.enterprise.policy import PolicyEngine

    engine = PolicyEngine()
    result = engine.enforce(model)

    print("TOKENPAK  |  Policy Enforcement")
    print(_SEP)
    print()
    print(f"  Model   : {model}")
    print(f"  Allowed : {'yes' if result.allowed else 'NO'}")
    if result.matched_policy:
        print(f"  Policy  : {result.matched_policy.id} ({result.action.value})")
    print(f"  Reason  : {result.reason or 'No matching policy'}")
    if result.reroute_to:
        print(f"  Reroute : {result.reroute_to}")
    print()


def run(argv: list[str] | None = None) -> None:
    """Entry point for 'tokenpak policy' command."""
    args = argv if argv is not None else sys.argv[2:]

    if not args:
        print("Usage: tokenpak policy <show|set|enforce> [options]")
        print()
        print("  show              List all policies")
        print("  set               Create or update a policy")
        print("  enforce <model>   Test enforcement for a model")
        print()
        return

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "show":
        _run_show()
    elif subcmd == "set":
        _run_set(rest)
    elif subcmd == "enforce":
        if not rest:
            print("Usage: tokenpak policy enforce <model>", file=sys.stderr)
            sys.exit(1)
        _run_enforce(rest[0], rest[1:])
    else:
        print(f"Unknown policy subcommand: {subcmd}", file=sys.stderr)
        print("Try: tokenpak policy show|set|enforce", file=sys.stderr)
        sys.exit(1)
