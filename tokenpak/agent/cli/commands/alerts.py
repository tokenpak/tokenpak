"""tokenpak alerts — alert channel test commands."""

from __future__ import annotations

import json
import sys


def run_alerts_cmd(argv: list[str]) -> None:
    """Entry point for `tokenpak alerts <subcommand>`."""
    import argparse

    ap = argparse.ArgumentParser(prog="tokenpak alerts", add_help=True)
    sub = ap.add_subparsers(dest="alerts_cmd")

    # tokenpak alerts test --channel webhook --url <url>
    # tokenpak alerts test --channel slack --webhook <url>
    tp = sub.add_parser("test", help="Test an alert delivery channel")
    tp.add_argument(
        "--channel",
        required=True,
        choices=["webhook", "slack"],
        help="Channel type to test",
    )
    tp.add_argument("--url", default=None, help="Destination URL (webhook channel)")
    tp.add_argument("--webhook", default=None, help="Slack incoming-webhook URL (slack channel)")

    args = ap.parse_args(argv)

    if args.alerts_cmd == "test":
        _run_test(args)
    else:
        ap.print_help()
        sys.exit(1)


def _run_test(args) -> None:
    """Send a test alert payload to the specified channel and report the result."""
    channel = args.channel

    if channel == "webhook":
        url = args.url
        if not url:
            print("Error: --url is required for --channel webhook", file=sys.stderr)
            sys.exit(1)
        _test_webhook(url)

    elif channel == "slack":
        webhook = args.webhook
        if not webhook:
            print("Error: --webhook is required for --channel slack", file=sys.stderr)
            sys.exit(1)
        _test_slack(webhook)


def _test_webhook(url: str) -> None:
    from tokenpak.alerts.channels.webhook import WebhookChannel

    payload_preview = {
        "event": "test",
        "severity": "info",
        "message": "TokenPak alert delivery test",
        "timestamp": "<utc-now>",
    }

    print(f"Sending webhook test to: {url}")
    print(f"Request body: {json.dumps(payload_preview, indent=2)}")

    ch = WebhookChannel(url)
    ok = ch.send(event="test", severity="info", message="TokenPak alert delivery test")

    if ok:
        print("✓ Webhook delivery succeeded")
    else:
        print("✖ Webhook delivery failed (see logs for details)", file=sys.stderr)
        sys.exit(1)


def _test_slack(webhook: str) -> None:
    from tokenpak.alerts.channels.slack import SlackChannel

    payload_preview = {"text": "ℹ️ *[INFO]* TokenPak alert delivery test"}

    print(f"Sending Slack test to: {webhook}")
    print(f"Request body: {json.dumps(payload_preview, indent=2)}")

    ch = SlackChannel(webhook)
    ok = ch.send(event="test", severity="info", message="TokenPak alert delivery test")

    if ok:
        print("✓ Slack delivery succeeded")
    else:
        print("✖ Slack delivery failed (see logs for details)", file=sys.stderr)
        sys.exit(1)
