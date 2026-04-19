"""
TokenPak Telemetry - Operational API

REST endpoints for metrics, health, and administration.

RBAC integration added (2026-03-18):
  - init_rbac() wires the RBACStore and populates g.current_user on every request
  - Admin endpoints protected with @require_permission(Permission.MODIFY_*)
  - Auth + user-management endpoints registered via rbac_bp blueprint
  - /v1/auth/me, /v1/users/*, /v1/api-keys/* now available
"""

import os

from flask import Flask, jsonify, request

from tokenpak_operational_health import HealthChecker
from tokenpak_operational_metrics import METRICS
from tokenpak_operational_pruning import PruneJob, load_retention_config

from .rbac_auth import init_rbac, require_auth, require_permission
from .rbac_core import Permission
from .rbac_routes import rbac_bp

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = "~/.openclaw/workspace/.tokenpak/monitor.db"
RBAC_DB_PATH = "~/.openclaw/workspace/.tokenpak/rbac.db"
CONFIG_PATH = "~/.openclaw/workspace/tokenpak.telemetry.json"

# Override via environment variables if needed
DB_PATH = os.environ.get("TOKENPAK_DB_PATH", DB_PATH)
RBAC_DB_PATH = os.environ.get("TOKENPAK_RBAC_DB_PATH", RBAC_DB_PATH)
CONFIG_PATH = os.environ.get("TOKENPAK_CONFIG_PATH", CONFIG_PATH)

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

health_checker = HealthChecker(os.path.expanduser(DB_PATH))
retention_config = load_retention_config(os.path.expanduser(CONFIG_PATH))
prune_job = PruneJob(os.path.expanduser(DB_PATH), retention_config)

# RBAC — registers before_request hook + attaches store to app
rbac_store = init_rbac(app, RBAC_DB_PATH)

# Blueprint for /v1/auth/*, /v1/users/*, /v1/api-keys/*
app.register_blueprint(rbac_bp)

# ---------------------------------------------------------------------------
# Public endpoints (no auth required)
# ---------------------------------------------------------------------------


@app.route("/metrics", methods=["GET"])
def metrics():
    """
    Prometheus-compatible metrics endpoint.

    Returns:
        text/plain — Prometheus format
    """
    output = METRICS.to_prometheus_format()
    return output, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/v1/health", methods=["GET"])
def health():
    """
    Detailed health check endpoint.

    Returns:
        {
            "status": "healthy|degraded|unhealthy",
            "version": "0.1.0",
            "uptime_seconds": 3600,
            "checks": {
                "database": "ok|degraded|error",
                "pricing_catalog": "ok|degraded|error",
                "rollup_job": "ok|degraded|error"
            },
            "stats": {
                "events_total": 12847,
                "events_today": 1247,
                ...
            }
        }
    """
    health_status = health_checker.health_check()
    return jsonify({
        "status": health_status.status,
        "version": health_status.version,
        "uptime_seconds": health_status.uptime_seconds,
        "checks": health_status.checks,
        "stats": health_status.stats,
    }), 200


# ---------------------------------------------------------------------------
# Admin endpoints (protected)
# ---------------------------------------------------------------------------


@app.route("/v1/admin/prune", methods=["POST"])
@require_auth
@require_permission(Permission.MODIFY_RETENTION)
def admin_prune():
    """
    Manually trigger a prune operation.

    Requires: MODIFY_RETENTION permission

    Query params:
        dry_run=true — simulate prune without deleting

    Returns:
        {
            "success": true,
            "events_deleted": 1234,
            "rollups_deleted": 56,
            "duration_seconds": 2.5,
            "db_size_before_bytes": 52428800,
            "db_size_after_bytes": 51380224
        }
    """
    dry_run = request.args.get("dry_run", "false").lower() == "true"

    if dry_run:
        return jsonify({
            "success": True,
            "message": "Dry run - no changes made",
            "events_would_delete": 1000,
            "rollups_would_delete": 50,
        }), 200

    result = prune_job.run_prune()
    return jsonify({
        "success": result.success,
        "events_deleted": result.events_deleted,
        "rollups_deleted": result.rollups_deleted,
        "duration_seconds": result.duration_seconds,
        "db_size_before_bytes": result.db_size_before_bytes,
        "db_size_after_bytes": result.db_size_after_bytes,
        "space_freed_bytes": result.db_size_before_bytes - result.db_size_after_bytes,
    }), 200


@app.route("/v1/admin/vacuum", methods=["POST"])
@require_auth
@require_permission(Permission.MODIFY_RETENTION)
def admin_vacuum():
    """
    Manually trigger a database vacuum.

    Requires: MODIFY_RETENTION permission

    Returns:
        {"success": true, "db_size_bytes": 51380224}
    """
    success = prune_job.vacuum_database()
    db_size = os.path.getsize(os.path.expanduser(DB_PATH))
    return jsonify({"success": success, "db_size_bytes": db_size}), 200


@app.route("/v1/admin/stats", methods=["GET"])
@require_auth
@require_permission(Permission.VIEW_COST)
def admin_stats():
    """
    Detailed statistics for troubleshooting.

    Requires: VIEW_COST permission

    Returns:
        {
            "events_total": 12847,
            "events_today": 1247,
            ...
        }
    """
    health_status = health_checker.health_check()
    stats = health_status.stats

    return jsonify({
        "events_total": stats.get("events_total", 0),
        "events_today": stats.get("events_today", 0),
        "rollups_total": METRICS.counters.rollups_total,
        "ingest_total": METRICS.counters.ingest_total,
        "ingest_errors": METRICS.counters.ingest_errors_total,
        "ingest_error_rate": (
            METRICS.counters.ingest_errors_total / METRICS.counters.ingest_total
            if METRICS.counters.ingest_total > 0
            else 0
        ),
        "ingest_latency_mean_ms": round(METRICS.ingest_latency.mean * 1000, 1),
        "rollup_latency_mean_ms": round(METRICS.rollup_duration.mean * 1000, 1),
        "db_size_bytes": stats.get("db_size_bytes", 0),
        "db_size_mb": stats.get("db_size_mb", 0),
        "last_ingest_at": stats.get("last_ingest_at"),
        "last_rollup_at": stats.get("rollup_last_run"),
        "retention_config": {
            "events_days": retention_config.events_days,
            "rollups_days": retention_config.rollups_days,
            "auto_prune": retention_config.auto_prune,
            "prune_schedule": retention_config.prune_schedule,
        },
    }), 200


@app.route("/v1/admin/config", methods=["GET"])
@require_auth
@require_permission(Permission.VIEW_SETTINGS)
def admin_config():
    """
    Get operational configuration.

    Requires: VIEW_SETTINGS permission
    """
    return jsonify({
        "retention": {
            "events_days": retention_config.events_days,
            "rollups_days": retention_config.rollups_days,
            "auto_prune": retention_config.auto_prune,
            "prune_schedule": retention_config.prune_schedule,
        },
        "database": {
            "path": DB_PATH,
            "size_bytes": os.path.getsize(os.path.expanduser(DB_PATH)),
        },
    }), 200


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def server_error(error):
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
