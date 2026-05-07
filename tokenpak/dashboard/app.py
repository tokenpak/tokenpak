"""tokenpak/agent/dashboard/app.py — Phase 5C Dashboard UI"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from tokenpak.telemetry.query.api import EntryStore
from tokenpak.telemetry.query.audit import AuditGenerator
from tokenpak.telemetry.query.timeline import TimelineGenerator
from tokenpak.dashboard.settings_persistence import (
    load_settings_context,
    validate_settings,
    write_settings,
)

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Expose Python builtins needed by templates (e.g., max/min in timeline.html)
templates.env.globals.update({"max": max, "min": min, "abs": abs, "round": round})
_store = EntryStore()
_timeline = TimelineGenerator()
_audit = AuditGenerator()


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")


# Consumption-mode panels (CCI-09). Keep the mode catalog as the single
# source of truth so routes, templates, and tests discover the same modes.
MODE_PANELS: tuple[dict[str, str], ...] = (
    {"mode": "cli", "label": "CLI", "template": "partials/mode_cli.html"},
    {"mode": "tui", "label": "TUI", "template": "partials/mode_tui.html"},
    {"mode": "tmux", "label": "tmux", "template": "partials/mode_tmux.html"},
    {"mode": "sdk", "label": "SDK", "template": "partials/mode_sdk.html"},
    {"mode": "ide", "label": "IDE", "template": "partials/mode_ide.html"},
    {"mode": "cron", "label": "cron", "template": "partials/mode_cron.html"},
)
VALID_MODES = frozenset(panel["mode"] for panel in MODE_PANELS)
_MODE_BY_NAME = {panel["mode"]: panel for panel in MODE_PANELS}
_PROFILE_PREFIX = "claude-code-"


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")


def _as_number(value: Any, default: float = 0.0) -> float:
    """Best-effort numeric conversion for telemetry rows with partial fields."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    """Best-effort integer conversion for telemetry rows with partial fields."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mode_from_profile(profile: str | None) -> str | None:
    """Map a CCI-04 profile name (for example claude-code-tui) to a mode."""
    if not profile:
        return None
    normalized = profile.lower().strip()
    if normalized in VALID_MODES:
        return normalized
    if normalized.startswith(_PROFILE_PREFIX):
        mode = normalized[len(_PROFILE_PREFIX):]
        if mode in VALID_MODES:
            return mode
    return None


def _active_profile_from_env(default_mode: str = "cli") -> str:
    """Return the CCI-04 profile label shown in the shared dashboard header."""
    for key in ("TOKENPAK_ACTIVE_PROFILE", "TOKENPAK_PROFILE", "TOKENPAK_COMPANION_PROFILE"):
        value = os.environ.get(key)
        if value:
            return value
    mode = os.environ.get("TOKENPAK_CONSUMPTION_MODE") or os.environ.get("TOKENPAK_MODE")
    detected = _mode_from_profile(mode) or default_mode
    return f"{_PROFILE_PREFIX}{detected}"


def _detect_active_mode() -> str:
    """Infer the consumption mode from CCI-04 profile/env signals."""
    for key in (
        "TOKENPAK_ACTIVE_PROFILE",
        "TOKENPAK_PROFILE",
        "TOKENPAK_COMPANION_PROFILE",
        "TOKENPAK_CONSUMPTION_MODE",
        "TOKENPAK_MODE",
    ):
        detected = _mode_from_profile(os.environ.get(key))
        if detected:
            return detected

    client = os.environ.get("TOKENPAK_CLIENT", "").lower()
    if client == "sdk" or os.environ.get("TOKENPAK_SDK"):
        return "sdk"
    if os.environ.get("TOKENPAK_JOB_NAME") or os.environ.get("CRON_JOB"):
        return "cron"
    if os.environ.get("VSCODE_PID") or os.environ.get("JETBRAINS_IDE"):
        return "ide"
    if os.environ.get("TMUX") or os.environ.get("TMUX_PANE"):
        return "tmux"
    if os.environ.get("TERM_PROGRAM", "").lower() in (
        "apple_terminal",
        "ghostty",
        "iterm.app",
        "wezterm",
    ):
        return "tui"
    return "cli"


def _mode_data(mode: str, date_str: str) -> dict[str, Any]:
    """Build mode-specific context dict for the per-mode template."""
    stats = _store.compute_stats(date_str)
    summary = _store.usage_summary(date_str)
    entries = _store.read_entries(date_str, date_str)

    header = {
        "total_cost": _as_number(stats.get("total_cost")),
        "cache_hit_pct": _as_number(stats.get("cache_hit_pct")),
        "request_count": _as_int(stats.get("request_count")),
    }

    active_panel = _MODE_BY_NAME[mode]
    ctx: dict[str, Any] = {
        "active_mode": mode,
        "active_panel": active_panel,
        "active_profile": _active_profile_from_env(mode),
        "mode_options": MODE_PANELS,
        "header": header,
        "stats": stats,
        "summary": summary,
        "query_date": date_str,
    }

    builders = {
        "cli": _cli_data,
        "tui": _tui_data,
        "tmux": lambda rows: _tmux_data(rows, stats),
        "sdk": _sdk_data,
        "ide": lambda rows: _ide_data(rows),
        "cron": _cron_data,
    }
    ctx.update(builders[mode](entries))
    return ctx


def _cli_data(entries: list[dict[str, Any]]) -> dict[str, Any]:
    repo_map: dict[str, dict[str, Any]] = {}
    for entry in entries:
        extra = entry.get("extra") or {}
        repo = extra.get("working_dir") or extra.get("repo_hash") or ""
        if not repo:
            continue
        row = repo_map.setdefault(repo, {"repo": repo, "request_count": 0, "cost": 0.0})
        row["request_count"] += 1
        row["cost"] += _as_number(entry.get("cost"))

    cost_by_repo = sorted(repo_map.values(), key=lambda x: x["cost"], reverse=True)[:10]
    return {
        "cost_by_repo": cost_by_repo,
        "burn_rate": None,
        "doctor_runs": [],
    }


def _tui_data(entries: list[dict[str, Any]]) -> dict[str, Any]:
    seen: dict[str, dict[str, Any]] = {}
    for entry in entries:
        sid = entry.get("session_id") or ""
        if not sid:
            continue
        row = seen.setdefault(sid, {"session_id": sid, "tokens": 0, "cost": 0.0, "saved_pct": 0.0})
        row["tokens"] += _as_int(entry.get("tokens"))
        row["cost"] += _as_number(entry.get("cost"))
        row["saved_pct"] = max(row["saved_pct"], _as_number((entry.get("extra") or {}).get("compression_pct")))

    tape = sorted(seen.values(), key=lambda x: x["cost"], reverse=True)[:10]
    total_cost = sum(_as_number(entry.get("cost")) for entry in entries)
    total_tokens = sum(_as_int(entry.get("tokens")) for entry in entries)
    return {
        "session_cost": total_cost,
        "session_tokens": total_tokens,
        "savings_pct": None,
        "savings_tape": tape,
    }


def _tmux_data(entries: list[dict[str, Any]], stats: dict[str, Any]) -> dict[str, Any]:
    agent_map: dict[str, dict[str, Any]] = {}
    for entry in entries:
        agent_id = entry.get("agent") or entry.get("session_id") or "unknown"
        row = agent_map.setdefault(
            agent_id,
            {"agent_id": agent_id, "request_count": 0, "total_tokens": 0, "cost": 0.0},
        )
        row["request_count"] += 1
        row["total_tokens"] += _as_int(entry.get("tokens"))
        row["cost"] += _as_number(entry.get("cost"))

    total_cost = max(_as_number(stats.get("total_cost")), 0.000000001)
    agent_rows = sorted(agent_map.values(), key=lambda x: x["cost"], reverse=True)[:20]
    for row in agent_rows:
        row["budget_pct"] = (row["cost"] / total_cost) * 100

    fairness_ok = all(row["budget_pct"] <= 70 for row in agent_rows)
    return {
        "agent_rows": agent_rows,
        "concurrent_sessions": len(agent_map),
        "fairness_ok": fairness_ok,
    }


def _sdk_data(entries: list[dict[str, Any]]) -> dict[str, Any]:
    model_map: dict[str, dict[str, Any]] = {}
    error_count = 0
    for entry in entries:
        model = entry.get("model") or "unknown"
        row = model_map.setdefault(
            model,
            {"model": model, "request_count": 0, "total_tokens": 0, "cost": 0.0},
        )
        row["request_count"] += 1
        row["total_tokens"] += _as_int(entry.get("tokens"))
        row["cost"] += _as_number(entry.get("cost"))
        status = str(entry.get("status") or (entry.get("extra") or {}).get("status") or "").lower()
        if status in {"error", "failed", "failure"} or entry.get("error"):
            error_count += 1

    otlp_endpoint = os.environ.get("TOKENPAK_OTLP_ENDPOINT", "")
    otlp_status = "active" if otlp_endpoint else "not configured"

    return {
        "model_usage": sorted(model_map.values(), key=lambda x: x["request_count"], reverse=True),
        "error_count": error_count,
        "otlp_status": otlp_status,
    }


def _ide_data(entries: list[dict[str, Any]]) -> dict[str, Any]:
    workspace = (
        os.environ.get("VSCODE_WORKSPACE_FOLDER")
        or os.environ.get("JETBRAINS_PROJECT")
        or os.environ.get("TOKENPAK_WORKSPACE")
        or ""
    )
    inline_savings_tokens = sum(_as_int((entry.get("extra") or {}).get("tokens_saved")) for entry in entries)
    inline_savings_cost = sum(_as_number((entry.get("extra") or {}).get("cost_saved")) for entry in entries)
    timeout_events = [
        {
            "timestamp": entry.get("timestamp", "unknown"),
            "description": entry.get("error") or (entry.get("extra") or {}).get("description") or "timeout",
        }
        for entry in entries
        if "timeout" in str(entry.get("error") or (entry.get("extra") or {}).get("error") or "").lower()
    ]
    return {
        "active_workspace": workspace or None,
        "inline_savings_tokens": inline_savings_tokens or None,
        "inline_savings_cost": inline_savings_cost or None,
        "timeout_count": len(timeout_events),
        "timeout_events": timeout_events,
    }


def _cron_data(entries: list[dict[str, Any]]) -> dict[str, Any]:
    job_map: dict[str, dict[str, Any]] = {}
    telegram_alerts = 0
    budget_enforcements = 0
    for entry in entries:
        extra = entry.get("extra") or {}
        job_name = extra.get("job_name") or entry.get("job_name")
        if job_name:
            row = job_map.setdefault(job_name, {"name": job_name, "total": 0, "success": 0, "failed": 0})
            row["total"] += 1
            status = str(entry.get("status") or extra.get("status") or "success").lower()
            if status in {"error", "failed", "failure"}:
                row["failed"] += 1
            else:
                row["success"] += 1
        if extra.get("telegram_alert_sent") or extra.get("alert_channel") == "telegram":
            telegram_alerts += 1
        if extra.get("budget_enforced") or extra.get("budget_hard_stop"):
            budget_enforcements += 1

    job_stats = sorted(job_map.values(), key=lambda x: x["total"], reverse=True)
    for row in job_stats:
        row["rate"] = (row["success"] / max(row["total"], 1)) * 100
    total_jobs = sum(row["total"] for row in job_stats)
    success_rate = (sum(row["success"] for row in job_stats) / total_jobs * 100) if total_jobs else None
    return {
        "job_stats": job_stats,
        "success_rate": success_rate,
        "telegram_alerts": telegram_alerts,
        "budget_enforcements": budget_enforcements,
    }


def _overview_data(query_date: str) -> dict:
    stats = _store.compute_stats(query_date)
    top_users = _store.top_users(query_date, limit=10)
    summary = _store.usage_summary(query_date)
    trend_start = (date.fromisoformat(query_date) - timedelta(days=6)).strftime("%Y-%m-%d")
    cache_trends = _store.cache_trends(trend_start, query_date)
    return {
        "stats": stats,
        "top_users": top_users,
        "summary": summary,
        "cache_trends": cache_trends,
    }


router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_overview(
    request: Request,
    date_str: Optional[str] = Query(None, alias="date"),
    mode: Optional[str] = Query(None),
):
    """Main dashboard — overview or per-mode view when ?mode= is provided."""
    q = date_str or _today()
    if mode is not None:
        if mode not in VALID_MODES:
            raise HTTPException(status_code=404, detail=f"Unknown mode: {mode!r}")
        ctx = _mode_data(mode, q)
        return templates.TemplateResponse(request, "per_mode.html", ctx)
    # Default: detect mode from environment (CCI-04 active profile)
    detected = _detect_active_mode()
    ctx = _mode_data(detected, q)
    return templates.TemplateResponse(request, "per_mode.html", ctx)


@router.get("/dashboard/htmx/mode/tui/cost", response_class=HTMLResponse)
def htmx_tui_cost(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    """HTMX partial — live cost figure for TUI mode polling (5s interval)."""
    q = date_str or _today()
    stats = _store.compute_stats(q)
    cost = stats.get("total_cost", 0.0)
    return HTMLResponse(content=f"${cost:.6f}")


@router.get("/dashboard/time-series", response_class=HTMLResponse)
def dashboard_time_series(
    request: Request,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    window: int = Query(5, ge=1, le=1440),
):
    """Time-series graphs page."""
    end_date = end or _today()
    start_date = start or _days_ago(6)
    rollups = _store.compute_rollups(start_date, end_date, window_minutes=window)
    return templates.TemplateResponse(
        request,
        "time_series.html",
        {
            "start_date": start_date,
            "end_date": end_date,
            "window": window,
            "rollup_count": len(rollups),
            "chart_labels": json.dumps([r["timestamp"] for r in rollups]),
            "tokens_data": json.dumps([r["total_tokens"] for r in rollups]),
            "cache_data": json.dumps([r["cache_tokens"] for r in rollups]),
            "requests_data": json.dumps([r["request_count"] for r in rollups]),
        },
    )


@router.get("/dashboard/agents", response_class=HTMLResponse)
def dashboard_agents(
    request: Request,
    date_str: Optional[str] = Query(None, alias="date"),
    sort: str = Query("requests"),
):
    """Agent leaderboard page."""
    q = date_str or _today()
    top_users = _store.top_users(q, limit=50)
    comp_map = {c["agent_id"]: c for c in _store.compression_ratios(q)}
    rows = []
    for u in top_users:
        aid = u["agent_id"]
        c = comp_map.get(aid, {})
        rows.append(
            {
                "agent_id": aid,
                "request_count": u["request_count"],
                "total_tokens": u["total_tokens"],
                "avg_compression_ratio": c.get("avg_compression_ratio", 0.0),
                "sample_count": c.get("sample_count", 0),
            }
        )
    sort_keys = {
        "requests": lambda x: x["request_count"],
        "tokens": lambda x: x["total_tokens"],
        "compression": lambda x: x["avg_compression_ratio"],
    }
    rows.sort(key=sort_keys.get(sort, sort_keys["requests"]), reverse=True)
    return templates.TemplateResponse(
        request, "agents.html", {"query_date": q, "today": _today(), "sort": sort, "rows": rows}
    )


@router.get("/dashboard/htmx/stats", response_class=HTMLResponse)
def htmx_stats(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    q = date_str or _today()
    return templates.TemplateResponse(
        request,
        "partials/stat_cards.html",
        {"stats": _store.compute_stats(q), "summary": _store.usage_summary(q), "query_date": q},
    )


@router.get("/dashboard/htmx/top-users", response_class=HTMLResponse)
def htmx_top_users(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    q = date_str or _today()
    return templates.TemplateResponse(
        request,
        "partials/top_users.html",
        {"top_users": _store.top_users(q, limit=10), "query_date": q},
    )


@router.get("/dashboard/timeline", response_class=HTMLResponse)
def dashboard_timeline(request: Request, hours: int = Query(24, ge=1, le=168)):
    """Timeline dashboard — hourly cost visualization for the last N hours."""
    # Load entries from last N hours
    start_date = _days_ago(hours // 24 + 1)
    end_date = _today()
    entries = _store.read_entries(start_date, end_date)

    # Generate hourly buckets
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    buckets = _timeline.hourly_buckets(entries, start_hour=now, num_hours=hours)

    # Extract chart data
    chart_labels = json.dumps([b["hour"] for b in buckets])
    chart_costs = json.dumps([round(b["cost"], 4) for b in buckets])
    chart_requests = json.dumps([b["requests"] for b in buckets])

    return templates.TemplateResponse(
        request,
        "timeline.html",
        {
            "hours": hours,
            "bucket_count": len(buckets),
            "total_cost": sum(b["cost"] for b in buckets),
            "total_requests": sum(b["requests"] for b in buckets),
            "chart_labels": chart_labels,
            "chart_costs": chart_costs,
            "chart_requests": chart_requests,
        },
    )


@router.get("/dashboard/api/timeline-data", response_class=JSONResponse)
def api_timeline_data(
    hours: int = Query(24, ge=1, le=168),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """JSON endpoint for timeline data."""
    if not start_date or not end_date:
        end_date = _today()
        start_date = _days_ago(hours // 24 + 1)

    entries = _store.read_entries(start_date, end_date)
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    buckets = _timeline.hourly_buckets(entries, start_hour=now, num_hours=hours)

    return {
        "hours": hours,
        "buckets": buckets,
        "summary": {
            "total_cost": sum(b["cost"] for b in buckets),
            "total_requests": sum(b["requests"] for b in buckets),
            "avg_request_cost": sum(b["cost"] for b in buckets)
            / max(sum(b["requests"] for b in buckets), 1),
        },
    }


@router.get("/dashboard/audit", response_class=HTMLResponse)
def dashboard_audit(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    """Audit dashboard — cost breakdown by model and feature."""
    q = date_str or _today()
    entries = _store.read_entries(q, q)
    audit = _audit.session_audit(entries)

    # Prepare chart data
    models_labels = json.dumps(list(audit["models"].keys()))
    models_costs = json.dumps([audit["models"][m]["cost"] for m in audit["models"].keys()])
    models_pcts = json.dumps([audit["models"][m]["percentage"] for m in audit["models"].keys()])

    features_labels = json.dumps(list(audit["features"].keys()))
    features_costs = json.dumps([audit["features"][f]["cost"] for f in audit["features"].keys()])
    features_pcts = json.dumps(
        [audit["features"][f]["percentage"] for f in audit["features"].keys()]
    )

    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "query_date": q,
            "today": _today(),
            "total_spend": audit["total_spend"],
            "request_count": audit["request_count"],
            "avg_cost": audit["avg_cost_per_request"],
            "models": audit["models"],
            "features": audit["features"],
            "models_labels": models_labels,
            "models_costs": models_costs,
            "models_pcts": models_pcts,
            "features_labels": features_labels,
            "features_costs": features_costs,
            "features_pcts": features_pcts,
        },
    )


@router.get("/dashboard/api/audit", response_class=JSONResponse)
def api_audit(
    date_str: Optional[str] = Query(None, alias="date"),
):
    """JSON endpoint for audit data."""
    q = date_str or _today()
    entries = _store.read_entries(q, q)
    audit = _audit.session_audit(entries)
    audit["generated_at"] = datetime.now(tz=timezone.utc).isoformat()
    return audit


@router.get("/settings/claude-code", response_class=HTMLResponse)
def settings_claude_code(request: Request):
    """CCI-13: Settings UI page for Claude Code integration."""
    ctx = load_settings_context()
    ctx["request"] = request
    ctx["page_title"] = "Claude Code Settings"
    ctx["active_profile_display"] = ctx.get("active_profile", "claude-code-cli")
    return templates.TemplateResponse(request, "settings_claude_code.html", ctx)


@router.post("/settings/claude-code/htmx/profile", response_class=HTMLResponse)
def htmx_settings_profile(
    request: Request,
    profile: str = Form(...),
):
    """HTMX: update active profile."""
    errors = validate_settings({"TOKENPAK_ACTIVE_PROFILE": profile})
    if errors:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(errors)}</p>', status_code=422
        )
    ok, write_errors = write_settings({"TOKENPAK_ACTIVE_PROFILE": profile})
    if not ok:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(write_errors)}</p>', status_code=422
        )
    return HTMLResponse(
        f'<span class="settings-ok">Profile set to <strong>{profile}</strong>. '
        f'Takes effect on next request.</span>'
    )


@router.post("/settings/claude-code/htmx/vault", response_class=HTMLResponse)
def htmx_settings_vault(
    request: Request,
    vault_inject_enabled: str = Form("0"),
    inject_budget: str = Form("4000"),
    inject_top_k: str = Form("5"),
    inject_min_score: str = Form("2.0"),
):
    """HTMX: update vault injection settings."""
    updates = {
        "TOKENPAK_VAULT_INJECT_ENABLED": vault_inject_enabled,
        "TOKENPAK_INJECT_BUDGET": inject_budget,
        "TOKENPAK_INJECT_TOP_K": inject_top_k,
        "TOKENPAK_INJECT_MIN_SCORE": inject_min_score,
    }
    errors = validate_settings(updates)
    if errors:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(errors)}</p>', status_code=422
        )
    ok, write_errors = write_settings(updates)
    if not ok:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(write_errors)}</p>', status_code=422
        )
    state = "enabled" if vault_inject_enabled in {"1", "true", "yes", "on"} else "disabled"
    return HTMLResponse(
        f'<span class="settings-ok">Vault injection {state}. '
        f'Budget {inject_budget} tokens, top_k={inject_top_k}, min_score={inject_min_score}. '
        f'Takes effect on next request.</span>'
    )


@router.post("/settings/claude-code/htmx/budget", response_class=HTMLResponse)
def htmx_settings_budget(
    request: Request,
    budget_controller_enabled: str = Form("0"),
    budget_total: str = Form("12000"),
):
    """HTMX: update budget enforcement settings."""
    updates = {
        "TOKENPAK_BUDGET_CONTROLLER": budget_controller_enabled,
        "TOKENPAK_BUDGET_TOTAL": budget_total,
    }
    errors = validate_settings(updates)
    if errors:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(errors)}</p>', status_code=422
        )
    ok, write_errors = write_settings(updates)
    if not ok:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(write_errors)}</p>', status_code=422
        )
    state = "enabled" if budget_controller_enabled in {"1", "true", "yes", "on"} else "disabled"
    return HTMLResponse(
        f'<span class="settings-ok">Budget enforcement {state}. '
        f'Monthly limit: {budget_total} tokens. Takes effect on next request.</span>'
    )


@router.post("/settings/claude-code/htmx/alerts", response_class=HTMLResponse)
def htmx_settings_alerts(
    request: Request,
    cache_alert_webhook_enabled: str = Form("0"),
    cache_alert_webhook_url: str = Form(""),
    cache_alert_slack_channel: str = Form(""),
    cache_alert_threshold: str = Form("50.0"),
):
    """HTMX: update cache invalidation alert settings."""
    updates = {
        "TOKENPAK_CACHE_ALERT_WEBHOOK_ENABLED": cache_alert_webhook_enabled,
        "TOKENPAK_CACHE_ALERT_THRESHOLD": cache_alert_threshold,
    }
    if cache_alert_webhook_url:
        return HTMLResponse(
            '<p class="settings-error">Webhook URL edits are disabled in the dashboard; '
            'edit tokenpak.env manually or request a webhook exception.</p>',
            status_code=422,
        )
    if cache_alert_slack_channel:
        updates["TOKENPAK_CACHE_ALERT_SLACK_CHANNEL"] = cache_alert_slack_channel

    errors = validate_settings(updates)
    if errors:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(errors)}</p>', status_code=422
        )
    ok, write_errors = write_settings(updates)
    if not ok:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(write_errors)}</p>', status_code=422
        )
    state = "enabled" if cache_alert_webhook_enabled in {"1", "true", "yes", "on"} else "disabled"
    return HTMLResponse(
        f'<span class="settings-ok">Cache invalidation alerts {state}. '
        f'Threshold: {cache_alert_threshold}%. Takes effect on next request.</span>'
    )


@router.post("/settings/claude-code/htmx/local-first", response_class=HTMLResponse)
def htmx_settings_local_first(
    request: Request,
    local_first_routing_enabled: str = Form("0"),
):
    """HTMX: update local-first routing toggle."""
    updates = {"TOKENPAK_LOCAL_FIRST_ROUTING": local_first_routing_enabled}
    errors = validate_settings(updates)
    if errors:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(errors)}</p>', status_code=422
        )
    ok, write_errors = write_settings(updates)
    if not ok:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(write_errors)}</p>', status_code=422
        )
    state = "enabled" if local_first_routing_enabled in {"1", "true", "yes", "on"} else "disabled"
    return HTMLResponse(
        f'<span class="settings-ok">Local-first routing {state}. '
        f'Restart proxy for this change to take effect.</span>'
    )


@router.post("/settings/claude-code/htmx/compliance", response_class=HTMLResponse)
def htmx_settings_compliance(
    request: Request,
    compliance_provider: str = Form(""),
):
    """HTMX: update compliance routing provider."""
    updates: dict[str, str] = {}
    if compliance_provider:
        updates["TOKENPAK_COMPLIANCE_PROVIDER"] = compliance_provider

    if not updates:
        return HTMLResponse('<span class="settings-ok">No changes.</span>')

    ok, write_errors = write_settings(updates, skip_validation=True)
    if not ok:
        return HTMLResponse(
            f'<p class="settings-error">{"; ".join(write_errors)}</p>', status_code=422
        )
    return HTMLResponse(
        f'<span class="settings-ok">Compliance provider set to <strong>{compliance_provider}</strong>. '
        f'Restart proxy for this change to take effect.</span>'
    )


@router.get("/settings/claude-code/api/current", response_class=JSONResponse)
def api_settings_current():
    """JSON: return current settings state (for testing/debugging)."""
    return load_settings_context()


def create_dashboard_app() -> FastAPI:
    app = FastAPI(title="TokenPak Dashboard", version="5.0.0")
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "tokenpak-dashboard"}

    return app


def create_combined_app() -> FastAPI:
    """Ingest + Query + Dashboard on port 17888."""
    from tokenpak.vault.ingest.api import router as ingest_router
    from tokenpak.telemetry.query.api import router as query_router

    app = FastAPI(title="TokenPak API + Dashboard", version="5.3.0")
    app.include_router(ingest_router)
    app.include_router(query_router)
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "tokenpak-combined", "version": "5.3.0"}

    return app
