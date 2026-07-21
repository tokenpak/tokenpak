"""Internal CLI handler for deterministic, process-local MemoryGuard optimization."""

from __future__ import annotations

import json
import sys
from typing import Any

from tokenpak.services import memory_optimization as optimizer

__all__: list[str] = []


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    action = payload["action"]
    print(f"TokenPak memory optimization — {action}")
    if action == "plan":
        plan = payload["plan"]
        guard = plan["memory_guard"]
        facts = plan["facts"]
        print(f"  Plan SHA-256 : {payload['plan_sha256']}")
        print(f"  Profile/mode : {plan['profile']} / {plan['mode']}")
        print(f"  Scope        : {plan['scope']} (no operating-system mutation)")
        print(f"  Effective RAM: {facts['effective_memory_bytes'] // optimizer.MIB} MiB")
        print(f"  Limit source : {facts['memory_limit_source']}")
        if plan["supported"] and guard["enabled"]:
            print(
                "  Guard         : "
                f"target={guard['target_mb']} MiB, ceiling={guard['ceiling_mb']} MiB, "
                f"system-low={guard['sys_low_mb']} MiB"
            )
        elif plan["mode"] == "off":
            print("  Guard         : disabled by this plan")
        else:
            print(f"  Unsupported   : {plan['support_reason']}")
    elif action == "apply":
        changed = "updated" if payload["changed"] else "already current"
        print(f"  Managed state : {changed}")
        print(f"  Plan SHA-256  : {payload['plan_sha256']}")
        print("  Scope         : process only; no operating-system settings changed")
        print("  Next          : restart the TokenPak proxy to load this plan")
    elif action == "status":
        print(f"  State         : {payload['status']['state']}")
        for name, path in sorted(payload["status"]["artifacts"].items()):
            print(f"  {name:<13}: {path}")
        config = payload["status"]["config"]
        if config.get("plan_sha256"):
            print(f"  Plan SHA-256  : {config['plan_sha256']}")
        if config.get("error"):
            print(f"  Config error  : {config['error']}")
        if payload["status"]["preimage"].get("error"):
            print(f"  Receipt error : {payload['status']['preimage']['error']}")
    elif action == "rollback":
        print(f"  Restored      : {payload['result']['restored']}")
        print("  Next          : restart the TokenPak proxy to load the restored state")


def _error(message: str, *, as_json: bool, code: int) -> int:
    if as_json:
        print(json.dumps({"error": message, "exit_code": code}, sort_keys=True))
    else:
        print(f"tokenpak config optimize: {message}", file=sys.stderr)
    return code


def cmd_config_optimize(args: Any) -> int:
    """Dispatch the exactly-one-action optimizer contract."""
    action = getattr(args, "optimize_action", None) or "plan"
    as_json = bool(getattr(args, "json", False))
    profile = getattr(args, "profile", "balanced")
    mode = getattr(args, "mode", "auto")
    expect_hash = getattr(args, "expect_hash", None)
    force = bool(getattr(args, "force", False))

    if force and action != "rollback":
        return _error(
            "--force is valid only with --rollback",
            as_json=as_json,
            code=optimizer.EXIT_APPLY_REFUSED,
        )
    if expect_hash and action != "apply":
        return _error(
            "--expect-hash is valid only with --apply",
            as_json=as_json,
            code=optimizer.EXIT_APPLY_REFUSED,
        )

    try:
        if action == "plan":
            plan = optimizer.build_plan(
                optimizer.probe_host_facts(),
                profile=profile,
                mode=mode,
            )
            wrapper = optimizer.wrap_plan(plan)
            _emit(
                {
                    "action": "plan",
                    "plan": wrapper["plan"],
                    "plan_sha256": wrapper["plan_sha256"],
                },
                as_json=as_json,
            )
            return optimizer.EXIT_OK if plan.supported else optimizer.EXIT_UNSUPPORTED

        if action == "apply":
            result = optimizer.apply_plan(
                profile=profile,
                mode=mode,
                expect_hash=expect_hash,
            )
            _emit({"action": "apply", **result}, as_json=as_json)
            return optimizer.EXIT_OK

        if action == "status":
            status = optimizer.optimizer_status()
            _emit({"action": "status", "status": status}, as_json=as_json)
            if status["state"] in {"corrupt_config", "corrupt_preimage"}:
                return optimizer.EXIT_CORRUPT
            return optimizer.EXIT_OK

        if action == "rollback":
            result = optimizer.rollback_plan(force=force)
            _emit({"action": "rollback", "result": result}, as_json=as_json)
            return optimizer.EXIT_OK
    except optimizer.UnsupportedHostError as exc:
        return _error(str(exc), as_json=as_json, code=optimizer.EXIT_UNSUPPORTED)
    except optimizer.ApplyRefusedError as exc:
        return _error(str(exc), as_json=as_json, code=optimizer.EXIT_APPLY_REFUSED)
    except optimizer.RollbackRefusedError as exc:
        return _error(str(exc), as_json=as_json, code=optimizer.EXIT_ROLLBACK_REFUSED)
    except optimizer.CorruptManagedConfigError as exc:
        return _error(str(exc), as_json=as_json, code=optimizer.EXIT_CORRUPT)
    except (OSError, ValueError, optimizer.OptimizationError) as exc:
        fallback = (
            optimizer.EXIT_ROLLBACK_REFUSED
            if action == "rollback"
            else optimizer.EXIT_APPLY_REFUSED
        )
        return _error(str(exc), as_json=as_json, code=fallback)

    return _error(
        f"unknown action {action!r}",
        as_json=as_json,
        code=optimizer.EXIT_APPLY_REFUSED,
    )
