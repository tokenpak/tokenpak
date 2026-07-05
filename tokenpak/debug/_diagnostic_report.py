# SPDX-License-Identifier: Apache-2.0
"""Sanitizer for local diagnostic report artifacts.

This module is intentionally local-only. It projects raw diagnostic inputs onto
an allowlisted ``diagnostic_report.v0`` schema and never submits, exports, or
bridges reports to an external service.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tokenpak import _paths
from tokenpak.security.dlp import DLPScanner

SCHEMA_VERSION = "diagnostic_report.v0"

_TOP_LEVEL = ("runtime", "command", "error", "logs", "config", "store")

_SAFE_DERIVATIVE_KEYS = {
    "request_hash",
    "response_hash",
    "report_hash",
    "report_sha256",
    "path_class",
    "path_hash",
    "env_var_present",
}

_HARD_REJECT_KEYS = {
    "api_key",
    "authorization",
    "body",
    "completion",
    "completions",
    "content",
    "cookie",
    "credential",
    "credentials",
    "env",
    "input",
    "messages",
    "output",
    "password",
    "payload",
    "prompt",
    "prompts",
    "refresh_token",
    "request",
    "response",
    "secret",
    "token",
}

_SECRET_VALUE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "token",
}

_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai-token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("anthropic-token", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}\b")),
)

_ABS_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("home", re.compile(r"(?<![\w.-])/(?:home|Users)/[^/\s:'\"]+(?:/[^\s:'\"]+)*"), "<home>"),
    ("tmp", re.compile(r"(?<![\w.-])/(?:tmp|var/tmp)/[^\s:'\"]+"), "<tmp>"),
    ("venv", re.compile(r"(?<![\w.-])/[^\s:'\"]*/\.venv[^\s:'\"]*"), "<venv>"),
    (
        "site-packages",
        re.compile(r"(?<![\w.-])/[^\s:'\"]*site-packages/[^\s:'\"]+"),
        "<site-packages>",
    ),
    ("windows-home", re.compile(r"\b[A-Za-z]:\\Users\\[^\\\s:'\"]+(?:\\[^\s:'\"]+)*"), "<home>"),
)


class DiagnosticReportError(ValueError):
    """Raised when a diagnostic report cannot be safely produced."""


def diagnostic_store_dir() -> Path:
    """Return the local diagnostic store directory path without creating it."""
    return _paths.resolved_home() / "debug" / "store"


def diagnostic_reports_dir() -> Path:
    """Return the local sanitized report directory path without creating it."""
    return _paths.resolved_home() / "debug" / "reports"


def diagnostic_receipts_dir() -> Path:
    """Return the local submission receipt directory path without creating it."""
    return _paths.resolved_home() / "debug" / "receipts"


def sanitize_diagnostic_report(
    raw: Mapping[str, Any],
    *,
    report_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Project *raw* onto the safe ``diagnostic_report.v0`` schema.

    Unsafe sections are omitted with manifest reason codes. If no useful
    diagnostic section survives sanitization, report generation fails closed.
    """
    if _contains_hard_reject_key(raw):
        # Top-level raw body exports such as encrypted capture records contain
        # request/response sections. They are not valid report sources.
        raw = {k: v for k, v in raw.items() if _key_is_safe(k)}

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "sanitizer_version": 1,
        "input_source_classes": sorted(k for k in raw.keys() if isinstance(k, str)),
        "retained_sections": [],
        "omitted_sections": [],
        "redaction_counts": {},
        "path_normalization_count": 0,
    }

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id or f"diag_{uuid.uuid4().hex[:16]}",
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "sanitizer_manifest": manifest,
    }

    runtime = _sanitize_runtime(raw.get("runtime"), manifest)
    if runtime:
        report["runtime"] = runtime
        manifest["retained_sections"].append("runtime")

    command = _sanitize_command(raw.get("command"), manifest)
    if command:
        report["command"] = command
        manifest["retained_sections"].append("command")

    error = _sanitize_error(raw.get("error"), manifest)
    if error:
        report["error"] = error
        manifest["retained_sections"].append("error")

    logs = _sanitize_logs(raw.get("logs"), manifest)
    if logs:
        report["logs"] = logs
        manifest["retained_sections"].append("logs")

    config = _sanitize_config(raw.get("config"), manifest)
    if config:
        report["config"] = config
        manifest["retained_sections"].append("config")

    store = _sanitize_store(raw.get("store"), manifest)
    if store:
        report["store"] = store
        manifest["retained_sections"].append("store")

    for section in _TOP_LEVEL:
        if section in raw and section not in manifest["retained_sections"]:
            _omit(manifest, section, "empty_or_unsafe")

    if not manifest["retained_sections"]:
        raise DiagnosticReportError("sanitized diagnostic report has no safe sections")

    payload_for_hash = {k: v for k, v in report.items() if k != "sanitizer_manifest"}
    manifest["final_report_byte_size"] = len(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    manifest["report_sha256"] = hashlib.sha256(
        json.dumps(payload_for_hash, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return report


def validate_sanitized_report(report: Mapping[str, Any]) -> None:
    """Fail if *report* is not a sanitized diagnostic report artifact."""
    if report.get("schema_version") != SCHEMA_VERSION:
        raise DiagnosticReportError("unsupported diagnostic report schema")
    manifest = report.get("sanitizer_manifest")
    if not isinstance(manifest, Mapping):
        raise DiagnosticReportError("diagnostic report missing sanitizer manifest")
    if _contains_hard_reject_key(report):
        raise DiagnosticReportError("diagnostic report contains disallowed keys")
    rendered = json.dumps(report, default=str)
    if _looks_like_raw_path(rendered):
        raise DiagnosticReportError("diagnostic report contains raw filesystem paths")


def _sanitize_runtime(value: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or _contains_hard_reject_key(value):
        _omit(manifest, "runtime", "unsafe_shape")
        return {}
    allowed = ("tokenpak_version", "python_version", "os_family", "arch", "install_source")
    return {k: str(value[k])[:120] for k in allowed if k in value and value[k] is not None}


def _sanitize_command(value: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or _contains_hard_reject_key(value):
        if value is not None:
            _omit(manifest, "command", "unsafe_shape")
        return {}
    out: dict[str, Any] = {}
    for key in ("group", "subcommand"):
        if key in value:
            out[key] = str(value[key])[:80]
    if "exit_code" in value:
        try:
            out["exit_code"] = int(value["exit_code"])
        except (TypeError, ValueError):
            out["exit_code"] = "unknown"
    if "duration_ms" in value:
        out["duration_bucket"] = _duration_bucket(value["duration_ms"])
    flags = value.get("flags")
    if isinstance(flags, Mapping):
        out["flags"] = {
            str(k)[:80]: _classify_value(v, str(k))
            for k, v in sorted(flags.items())
            if _key_is_safe(str(k))
        }
    return out


def _sanitize_error(value: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or _contains_hard_reject_key(value):
        if value is not None:
            _omit(manifest, "error", "unsafe_shape")
        return {}
    out: dict[str, Any] = {}
    if "exception_class" in value:
        out["exception_class"] = str(value["exception_class"])[:160]
    if "code" in value:
        out["code"] = str(value["code"])[:120]
    message = value.get("message_template") or value.get("message")
    if message:
        out["message_template"] = _safe_text(str(message), manifest, limit=500)
    stack = value.get("stack")
    if isinstance(stack, list):
        out["stack_signature"] = [
            hashlib.sha256(_safe_text(str(frame), manifest, limit=300).encode()).hexdigest()[:16]
            for frame in stack[:12]
        ]
    return out


def _sanitize_logs(value: Any, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        _omit(manifest, "logs", "unsafe_shape")
        return []
    entries: list[dict[str, Any]] = []
    for idx, item in enumerate(value[:20]):
        if isinstance(item, Mapping):
            if _contains_hard_reject_key(item):
                _omit(manifest, f"logs[{idx}]", "hard_reject_key")
                continue
            text = item.get("message") or item.get("line") or item.get("text")
        else:
            text = item
        if text is None:
            continue
        sanitized = _safe_text(str(text), manifest, limit=1000)
        if sanitized:
            entries.append({"message": sanitized, "bytes": len(sanitized.encode("utf-8"))})
    return entries


def _sanitize_config(value: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        if value is not None:
            _omit(manifest, "config", "unsafe_shape")
        return {}
    entries: dict[str, str] = {}
    for key, raw_value in sorted(value.items()):
        key_s = str(key)
        if not _key_is_safe(key_s):
            key_hash = hashlib.sha256(key_s.encode("utf-8")).hexdigest()[:12]
            entries[f"redacted_key_{key_hash}"] = "secret"
            _count(manifest, "config-secret-key")
            continue
        entries[key_s[:120]] = _classify_value(raw_value, key_s)
    return {"keys": entries} if entries else {}


def _sanitize_store(value: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or _contains_hard_reject_key(value):
        if value is not None:
            _omit(manifest, "store", "unsafe_shape")
        return {}
    out: dict[str, Any] = {}
    for key in ("record_count", "size_bytes", "ttl_seconds", "request_cap", "retention_policy"):
        if key in value:
            out[key] = _safe_scalar(value[key])
    return out


def _safe_text(text: str, manifest: dict[str, Any], *, limit: int) -> str:
    text, path_count = _normalize_paths(text)
    if path_count:
        manifest["path_normalization_count"] += path_count
    scanner = DLPScanner(mode="redact")
    for finding in scanner.scan(text):
        _count(manifest, finding.rule_id)
    text = scanner.redact(text)
    for rule_id, pattern in _TOKEN_PATTERNS:
        text, count = pattern.subn(f"[REDACTED:{rule_id}]", text)
        if count:
            _count(manifest, rule_id, count)
    return text[:limit]


def _normalize_paths(text: str) -> tuple[str, int]:
    count = 0
    home = str(Path.home())
    if home and home in text:
        text = text.replace(home, "<home>")
        count += 1
    for _, pattern, replacement in _ABS_PATH_PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n
    return text, count


def _contains_hard_reject_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_s = str(key)
            if not _key_is_safe(key_s):
                return True
            if _contains_hard_reject_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_hard_reject_key(item) for item in value)
    return False


def _key_is_safe(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _SAFE_DERIVATIVE_KEYS:
        return True
    return normalized not in _HARD_REJECT_KEYS


def _looks_like_raw_path(text: str) -> bool:
    return any(pattern.search(text) for _, pattern, _ in _ABS_PATH_PATTERNS)


def _classify_value(value: Any, key: str) -> str:
    normalized = key.lower().replace("-", "_")
    if normalized in _SECRET_VALUE_KEYS or any(part in normalized for part in ("secret", "token", "key", "password")):
        return "secret"
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str) and _looks_like_raw_path(value):
        return "path"
    return type(value).__name__


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3)
    return str(value)[:120]


def _duration_bucket(value: Any) -> str:
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if ms < 100:
        return "lt_100ms"
    if ms < 1000:
        return "lt_1s"
    if ms < 10000:
        return "lt_10s"
    return "gte_10s"


def _count(manifest: dict[str, Any], rule_id: str, amount: int = 1) -> None:
    counts = manifest.setdefault("redaction_counts", {})
    counts[rule_id] = int(counts.get(rule_id, 0)) + amount


def _omit(manifest: dict[str, Any], section: str, reason: str) -> None:
    omitted = manifest.setdefault("omitted_sections", [])
    item = {"section": section, "reason": reason}
    if item not in omitted:
        omitted.append(item)
