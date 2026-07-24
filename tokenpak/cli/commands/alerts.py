# SPDX-License-Identifier: Apache-2.0
"""alerts command — test and manage alert delivery channels."""

from __future__ import annotations

import json
import sys
from typing import Any


def cmd_alerts_test(args: Any) -> None:
    """Test an alert delivery channel by sending a sample payload."""
    channel = args.channel
    success = False

    if channel == "webhook":
        if not args.url:
            print("❌ --url is required for --channel webhook", file=sys.stderr)
            sys.exit(1)
        from tokenpak.alerts.channels import webhook

        request_body = json.loads(
            webhook._build_payload(
                event="test",
                severity="info",
                message="TokenPak alert delivery test",
                source="tokenpak alerts test",
            ).decode()
        )
        print(f"→ POSTing to {args.url}")
        print(f"  Body: {json.dumps(request_body, indent=2)}")
        success = webhook.deliver(
            args.url,
            event="test",
            severity="info",
            message="TokenPak alert delivery test",
            source="tokenpak alerts test",
        )

    elif channel == "slack":
        if not args.webhook:
            print("❌ --webhook is required for --channel slack", file=sys.stderr)
            sys.exit(1)
        from tokenpak.alerts.channels import slack

        request_body = {"text": slack._build_text("test", "info", "TokenPak alert delivery test")}
        print(f"→ POSTing to {args.webhook}")
        print(f"  Body: {json.dumps(request_body, indent=2)}")
        success = slack.deliver(
            args.webhook,
            event="test",
            severity="info",
            message="TokenPak alert delivery test",
        )

    else:
        print(f"❌ Unknown channel: {channel!r}. Use 'webhook' or 'slack'.", file=sys.stderr)
        sys.exit(1)

    if success:
        print("✅ Delivery succeeded")
    else:
        print("❌ Delivery failed (check logs for details)")
        sys.exit(1)
