"""
TokenPak Telemetry - Operational API

REST endpoints for authentication and administration.

RBAC integration added (2026-03-18):
  - init_rbac() wires the RBACStore and populates g.current_user on every request
  - Auth + user-management endpoints registered via rbac_bp blueprint
  - /v1/auth/me, /v1/users/*, /v1/api-keys/* now available

Historical note (2026-07-02): the former /metrics, /v1/health and
/v1/admin/{prune,vacuum,stats,config} endpoints were removed. They depended
on modules that were never importable and queried ``events`` / ``rollups``
tables that no code in this codebase ever creates, so every one of those
endpoints failed at request time. Retention/pruning and health surfaces
should be rebuilt against the real telemetry schema (``tp_events``,
``tp_rollup_daily_*``) if they are needed.
"""

import os

from flask import Flask, jsonify

from .rbac_auth import init_rbac
from .rbac_routes import rbac_bp

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RBAC_DB_PATH = "~/.tokenpak/data/rbac.db"
RBAC_DB_PATH = os.environ.get("TOKENPAK_RBAC_DB_PATH", RBAC_DB_PATH)

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

# RBAC — registers before_request hook + attaches store to app
rbac_store = init_rbac(app, RBAC_DB_PATH)

# Blueprint for /v1/auth/*, /v1/users/*, /v1/api-keys/*
app.register_blueprint(rbac_bp)

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
    app.run(host="127.0.0.1", port=5001, debug=False)
