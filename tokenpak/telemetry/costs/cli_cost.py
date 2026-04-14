"""
CLI commands for cost management.

Provides `tokenpak cost show-budget` for displaying budget status.
"""

import json

from tokenpak.telemetry.costs.budget_tracker import BudgetTracker


def cmd_cost_show_budget(args) -> int:
    """
    Display current budget status and spending progress.

    Usage:
        tokenpak cost show-budget
        tokenpak cost show-budget --config path/to/config.json
    """
    config_path = args.config if hasattr(args, "config") else None

    # Load config from file or use defaults
    config = {}
    if config_path:
        try:
            with open(config_path) as f:
                config_data = json.load(f)
                config = config_data.get("cost_budget", {})
        except Exception as e:
            print(f"Error loading config: {e}")
            return 1

    # Initialize tracker
    tracker = BudgetTracker(config)

    # Get summary
    summary = tracker.get_budget_summary()

    print("\n📊 TokenPak Budget Status\n" + "=" * 40)

    if not summary["enabled"]:
        print("Budget tracking: DISABLED")
        return 0

    # Display daily budget
    if summary["daily_limit"]:
        daily = summary["daily_limit"]
        print(f"Daily limit: ${daily:.2f}")
    else:
        print("Daily limit: Not configured")

    # Display weekly budget
    if summary["weekly_limit"]:
        weekly = summary["weekly_limit"]
        print(f"Weekly limit: ${weekly:.2f}")
    else:
        print("Weekly limit: Not configured")

    # Display alert settings
    print(f"Alert cooldown: {summary['alert_cooldown_minutes']:.0f} minutes")

    # Display recent alerts
    if summary["last_alerts"]:
        print("\nRecent Alerts:")
        for alert_key, timestamp in summary["last_alerts"].items():
            print(f"  • {alert_key}: {timestamp}")
    else:
        print("\nNo alerts triggered yet")

    print("=" * 40 + "\n")
    return 0


def register_cost_commands(subparsers):
    """Register `tokenpak cost` subcommands"""
    cost_parser = subparsers.add_parser(
        "cost",
        help="Manage API cost budgets and alerts",
    )
    cost_subparsers = cost_parser.add_subparsers(dest="cost_cmd")

    # tokenpak cost show-budget
    budget_parser = cost_subparsers.add_parser(
        "show-budget",
        help="Display budget status and spending progress",
    )
    budget_parser.add_argument(
        "--config",
        help="Path to tokenpak config file (default: tokenpak.json)",
    )
    budget_parser.set_defaults(func=cmd_cost_show_budget)

    return cost_parser
