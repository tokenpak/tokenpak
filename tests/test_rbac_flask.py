"""tests/test_rbac_flask.py

RBAC Flask Integration Tests
=============================
Tests authentication, user management, API keys, and permission enforcement.

Strategy: Build a minimal Flask app in-process using the real RBAC modules
(RBACStore, init_rbac, rbac_bp) without touching the heavy telemetry stack.
This avoids all the tokenpak_operational_* import issues.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, jsonify

from tokenpak.telemetry.operational.rbac_auth import (
    init_rbac,
    require_auth,
    require_permission,
)
from tokenpak.telemetry.operational.rbac_core import Permission, Role
from tokenpak.telemetry.operational.rbac_routes import rbac_bp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_app(rbac_db: str) -> Flask:
    """Build a minimal Flask app wired with RBAC + a few protected routes."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    # Wire RBAC
    init_rbac(app, rbac_db)

    # Register RBAC blueprint
    app.register_blueprint(rbac_bp)

    # Fake protected admin route
    @app.route("/v1/admin/prune", methods=["POST"])
    @require_auth
    @require_permission(Permission.MODIFY_RETENTION)
    def admin_prune():
        return jsonify({"success": True, "dry_run": True}), 200

    @app.route("/v1/admin/stats", methods=["GET"])
    @require_auth
    @require_permission(Permission.VIEW_COST)
    def admin_stats():
        return jsonify({"events_total": 42}), 200

    @app.route("/v1/admin/config", methods=["GET"])
    @require_auth
    @require_permission(Permission.VIEW_SETTINGS)
    def admin_config():
        return jsonify({"retention": {}}), 200

    @app.route("/v1/health", methods=["GET"])
    def health():
        return jsonify({"status": "healthy"}), 200

    return app


@pytest.fixture(scope="function")
def flask_app(tmp_path):
    rbac_db = str(tmp_path / "test_rbac.db")
    app = _build_app(rbac_db)
    client = app.test_client()
    store = app.rbac_store
    yield client, store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(client, store, role=Role.ADMIN, suffix=""):
    username = f"u_{role.value}{suffix}"
    password = "Test1234!"
    store.create_user(username, password, role)
    resp = client.post("/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["token"]


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth endpoint tests
# ---------------------------------------------------------------------------


class TestAuthLogin:
    def test_login_success(self, flask_app):
        client, store = flask_app
        store.create_user("alice", "secret123!", Role.ADMIN)
        resp = client.post("/v1/auth/login", json={"username": "alice", "password": "secret123!"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "token" in data
        assert data["user"]["username"] == "alice"
        assert data["user"]["role"] == "admin"

    def test_login_wrong_password(self, flask_app):
        client, store = flask_app
        store.create_user("bob", "correcthorse!", Role.READONLY)
        resp = client.post("/v1/auth/login", json={"username": "bob", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_unknown_user(self, flask_app):
        client, store = flask_app
        resp = client.post("/v1/auth/login", json={"username": "ghost", "password": "x"})
        assert resp.status_code == 401

    def test_login_missing_fields(self, flask_app):
        client, store = flask_app
        resp = client.post("/v1/auth/login", json={"username": "x"})
        assert resp.status_code == 400


class TestAuthMe:
    def test_me_returns_user_info(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.ENGINEER, "_me1")
        resp = client.get("/v1/auth/me", headers=_auth_headers(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["role"] == "engineer"
        assert "permissions" in data
        assert "view_dashboard" in data["permissions"]

    def test_me_requires_auth(self, flask_app):
        client, store = flask_app
        resp = client.get("/v1/auth/me")
        assert resp.status_code == 401


class TestAuthLogout:
    def test_logout_invalidates_session(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.ADMIN, "_logout1")
        headers = _auth_headers(token)
        # Before logout: works
        assert client.get("/v1/auth/me", headers=headers).status_code == 200
        # Logout
        resp = client.post("/v1/auth/logout", headers=headers)
        assert resp.status_code == 200
        # After logout: 401
        assert client.get("/v1/auth/me", headers=headers).status_code == 401


# ---------------------------------------------------------------------------
# User management tests
# ---------------------------------------------------------------------------


class TestUsers:
    def test_create_user_as_admin(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.ADMIN, "_cu1")
        resp = client.post("/v1/users", headers=_auth_headers(token),
                           json={"username": "newuser", "password": "Pass1234!", "role": "readonly"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["username"] == "newuser"
        assert data["role"] == "readonly"

    def test_create_user_forbidden_for_readonly(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.READONLY, "_cu2")
        resp = client.post("/v1/users", headers=_auth_headers(token),
                           json={"username": "another", "password": "Pass1234!", "role": "readonly"})
        assert resp.status_code == 403

    def test_create_user_invalid_role(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.ADMIN, "_cu3")
        resp = client.post("/v1/users", headers=_auth_headers(token),
                           json={"username": "x", "password": "y", "role": "superuser"})
        assert resp.status_code == 400

    def test_create_user_duplicate(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.ADMIN, "_cu4")
        payload = {"username": "dupuser", "password": "Pass1234!", "role": "readonly"}
        assert client.post("/v1/users", headers=_auth_headers(token), json=payload).status_code == 201
        resp = client.post("/v1/users", headers=_auth_headers(token), json=payload)
        assert resp.status_code == 409

    def test_list_users_admin(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.ADMIN, "_lu1")
        resp = client.get("/v1/users", headers=_auth_headers(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert "users" in data
        assert isinstance(data["total"], int)

    def test_list_users_forbidden(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.READONLY, "_lu2")
        resp = client.get("/v1/users", headers=_auth_headers(token))
        assert resp.status_code == 403

    def test_get_own_user(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.READONLY, "_gu1")
        me = client.get("/v1/auth/me", headers=_auth_headers(token)).get_json()
        resp = client.get(f"/v1/users/{me['id']}", headers=_auth_headers(token))
        assert resp.status_code == 200
        assert resp.get_json()["id"] == me["id"]

    def test_update_user_role(self, flask_app):
        client, store = flask_app
        admin_token = _login(client, store, Role.ADMIN, "_uu1")
        create_resp = client.post("/v1/users", headers=_auth_headers(admin_token),
                                  json={"username": "target_u", "password": "Pass1234!", "role": "readonly"})
        target_id = create_resp.get_json()["id"]
        resp = client.patch(f"/v1/users/{target_id}", headers=_auth_headers(admin_token),
                            json={"role": "finops"})
        assert resp.status_code == 200
        assert resp.get_json()["role"] == "finops"

    def test_deactivate_user(self, flask_app):
        client, store = flask_app
        admin_token = _login(client, store, Role.ADMIN, "_du1")
        create_resp = client.post("/v1/users", headers=_auth_headers(admin_token),
                                  json={"username": "del_target", "password": "Pass1234!", "role": "readonly"})
        target_id = create_resp.get_json()["id"]
        resp = client.delete(f"/v1/users/{target_id}", headers=_auth_headers(admin_token))
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "deactivated"

    def test_cannot_deactivate_self(self, flask_app):
        client, store = flask_app
        admin_token = _login(client, store, Role.ADMIN, "_self_del")
        me = client.get("/v1/auth/me", headers=_auth_headers(admin_token)).get_json()
        resp = client.delete(f"/v1/users/{me['id']}", headers=_auth_headers(admin_token))
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# API key tests
# ---------------------------------------------------------------------------


class TestAPIKeys:
    def test_create_and_list_key(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.ENGINEER, "_key1")
        headers = _auth_headers(token)

        # Create
        resp = client.post("/v1/api-keys", headers=headers, json={"name": "ci-key"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["key"].startswith("tp_")
        key_id = data["id"]

        # List
        resp2 = client.get("/v1/api-keys", headers=headers)
        assert resp2.status_code == 200
        ids = [k["id"] for k in resp2.get_json()["api_keys"]]
        assert key_id in ids

    def test_api_key_auth(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.ENGINEER, "_key2")
        headers = _auth_headers(token)

        create_resp = client.post("/v1/api-keys", headers=headers, json={"name": "test-key"})
        raw_key = create_resp.get_json()["key"]

        # Use the raw key to authenticate
        resp = client.get("/v1/auth/me", headers={"X-API-Key": raw_key})
        assert resp.status_code == 200

    def test_revoke_key(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.FINOPS, "_key3")
        headers = _auth_headers(token)

        create_resp = client.post("/v1/api-keys", headers=headers, json={"name": "revoke-me"})
        data = create_resp.get_json()
        raw_key = data["key"]
        key_id = data["id"]

        # Revoke
        resp = client.delete(f"/v1/api-keys/{key_id}", headers=headers)
        assert resp.status_code == 200

        # Key no longer works
        resp2 = client.get("/v1/auth/me", headers={"X-API-Key": raw_key})
        assert resp2.status_code == 401


# ---------------------------------------------------------------------------
# Admin route protection tests
# ---------------------------------------------------------------------------


class TestAdminProtection:
    def test_prune_requires_auth(self, flask_app):
        client, store = flask_app
        resp = client.post("/v1/admin/prune")
        assert resp.status_code == 401

    def test_prune_requires_permission(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.READONLY, "_prune1")
        resp = client.post("/v1/admin/prune", headers=_auth_headers(token))
        assert resp.status_code == 403

    def test_prune_allowed_for_admin(self, flask_app):
        client, store = flask_app
        token = _login(client, store, Role.ADMIN, "_prune2")
        resp = client.post("/v1/admin/prune", headers=_auth_headers(token))
        assert resp.status_code == 200

    def test_stats_requires_auth(self, flask_app):
        client, store = flask_app
        resp = client.get("/v1/admin/stats")
        assert resp.status_code == 401

    def test_stats_allowed_for_readonly(self, flask_app):
        """VIEW_COST is granted to READONLY role."""
        client, store = flask_app
        token = _login(client, store, Role.READONLY, "_stats1")
        resp = client.get("/v1/admin/stats", headers=_auth_headers(token))
        assert resp.status_code == 200

    def test_health_no_auth_needed(self, flask_app):
        """Health endpoint is intentionally public."""
        client, store = flask_app
        resp = client.get("/v1/health")
        assert resp.status_code == 200

    def test_config_requires_view_settings(self, flask_app):
        """No auth → 401."""
        client, store = flask_app
        resp = client.get("/v1/admin/config")
        assert resp.status_code == 401

    def test_config_allowed_with_auth(self, flask_app):
        """Any authenticated user has VIEW_SETTINGS."""
        client, store = flask_app
        token = _login(client, store, Role.READONLY, "_cfg1")
        resp = client.get("/v1/admin/config", headers=_auth_headers(token))
        assert resp.status_code == 200
