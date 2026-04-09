"""
TokenPak RBAC — Flask Blueprint for auth + user management endpoints.

Routes:
  POST   /v1/auth/login        — issue session token
  POST   /v1/auth/logout       — invalidate session
  GET    /v1/auth/me           — current user + permissions

  POST   /v1/users             — create user (MANAGE_USERS)
  GET    /v1/users             — list users with pagination (MANAGE_USERS)
  GET    /v1/users/<id>        — get single user (MANAGE_USERS or self)
  PATCH  /v1/users/<id>        — update role/email/status (MANAGE_USERS)
  DELETE /v1/users/<id>        — deactivate user (MANAGE_USERS)

  POST   /v1/api-keys          — generate API key
  GET    /v1/api-keys          — list caller's API keys
  DELETE /v1/api-keys/<id>     — revoke API key
"""

from flask import Blueprint, current_app, g, jsonify, request

from .rbac_auth import require_auth, require_permission
from .rbac_core import Permission, Role

rbac_bp = Blueprint("rbac", __name__)


def _store():
    """Fetch the RBACStore attached to the current app."""
    return current_app.rbac_store


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@rbac_bp.route("/v1/auth/login", methods=["POST"])
def auth_login():
    """
    Issue a session token.

    Body: {"username": "...", "password": "..."}

    Returns:
        {"token": "...", "expires_in_hours": 24, "user": {...}}
    """
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400

    user = _store().authenticate(username, password)
    if user is None:
        return jsonify({"error": "Invalid credentials"}), 401

    token = _store().create_session(
        user,
        ip=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    return jsonify(
        {
            "token": token,
            "expires_in_hours": 24,
            "user": {
                "id": user.id,
                "username": user.username,
                "role": user.role.value,
            },
        }
    ), 200


@rbac_bp.route("/v1/auth/logout", methods=["POST"])
@require_auth
def auth_logout():
    """Invalidate the current session token."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        _store().invalidate_session(token)
    return jsonify({"ok": True}), 200


@rbac_bp.route("/v1/auth/me", methods=["GET"])
@require_auth
def auth_me():
    """Return current user info and permissions."""
    user = g.current_user
    permissions = [p.value for p in user.role.__class__]
    try:
        from .rbac_core import PERMISSION_MATRIX

        permissions = [p.value for p in PERMISSION_MATRIX.get(user.role, set())]
    except Exception:
        pass

    return jsonify(
        {
            "id": user.id,
            "username": user.username,
            "role": user.role.value,
            "permissions": sorted(permissions),
            "last_login": user.last_login.isoformat() if user.last_login else None,
        }
    ), 200


# ---------------------------------------------------------------------------
# User management endpoints
# ---------------------------------------------------------------------------


@rbac_bp.route("/v1/users", methods=["POST"])
@require_auth
@require_permission(Permission.MANAGE_USERS)
def create_user():
    """Create a new user. Requires MANAGE_USERS permission."""
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role_str = data.get("role", "readonly")
    email = data.get("email")

    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400

    try:
        role = Role(role_str)
    except ValueError:
        return jsonify(
            {"error": f"Invalid role '{role_str}'", "valid_roles": [r.value for r in Role]}
        ), 400

    try:
        user = _store().create_user(
            username=username,
            password=password,
            role=role,
            email=email,
            created_by_id=g.current_user.id,
        )
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            return jsonify({"error": "Username or email already exists"}), 409
        raise

    return jsonify(
        {
            "id": user.id,
            "username": user.username,
            "role": user.role.value,
            "created_at": user.created_at.isoformat(),
        }
    ), 201


@rbac_bp.route("/v1/users", methods=["GET"])
@require_auth
@require_permission(Permission.MANAGE_USERS)
def list_users():
    """List all users with pagination."""
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return jsonify({"error": "limit and offset must be integers"}), 400

    users, total = _store().list_users(limit=limit, offset=offset)
    return jsonify(
        {
            "users": users,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    ), 200


@rbac_bp.route("/v1/users/<user_id>", methods=["GET"])
@require_auth
def get_user(user_id):
    """Get a single user. Admins can fetch any user; others can only fetch themselves."""
    caller = g.current_user
    if caller.id != user_id and not caller.has_permission(Permission.MANAGE_USERS):
        return jsonify({"error": "Forbidden"}), 403

    user = _store().get_user_by_id(user_id)
    if user is None:
        return jsonify({"error": "User not found"}), 404

    return jsonify(
        {
            "id": user.id,
            "username": user.username,
            "role": user.role.value,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None,
        }
    ), 200


@rbac_bp.route("/v1/users/<user_id>", methods=["PATCH"])
@require_auth
@require_permission(Permission.MANAGE_USERS)
def update_user(user_id):
    """Update user role, email, or active status."""
    data = request.get_json(silent=True) or {}

    role = data.get("role")
    email = data.get("email")
    is_active = data.get("is_active")

    if role is not None:
        try:
            Role(role)
        except ValueError:
            return jsonify(
                {"error": f"Invalid role '{role}'", "valid_roles": [r.value for r in Role]}
            ), 400

    user = _store().update_user(
        user_id,
        role=role,
        email=email,
        is_active=is_active,
        updated_by_id=g.current_user.id,
    )
    if user is None:
        return jsonify({"error": "User not found"}), 404

    return jsonify(
        {
            "id": user.id,
            "username": user.username,
            "role": user.role.value,
            "is_active": user.is_active,
        }
    ), 200


@rbac_bp.route("/v1/users/<user_id>", methods=["DELETE"])
@require_auth
@require_permission(Permission.MANAGE_USERS)
def deactivate_user(user_id):
    """Deactivate (soft-delete) a user."""
    caller = g.current_user
    if caller.id == user_id:
        return jsonify({"error": "Cannot deactivate yourself"}), 400

    _store().deactivate_user(user_id, deactivated_by_id=caller.id)
    return jsonify({"ok": True, "user_id": user_id, "status": "deactivated"}), 200


# ---------------------------------------------------------------------------
# API Key endpoints
# ---------------------------------------------------------------------------


@rbac_bp.route("/v1/api-keys", methods=["POST"])
@require_auth
def create_api_key():
    """Generate a new API key for the current user."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    expires_in_days = data.get("expires_in_days")

    raw_key, record = _store().create_api_key(
        user=g.current_user,
        name=name,
        expires_in_days=expires_in_days,
    )
    return jsonify(
        {
            "key": raw_key,
            "note": "Store this key securely — it will not be shown again.",
            **record,
        }
    ), 201


@rbac_bp.route("/v1/api-keys", methods=["GET"])
@require_auth
def list_api_keys():
    """List the current user's API keys (hashed — raw keys not shown)."""
    keys = _store().list_api_keys(g.current_user.id)
    return jsonify({"api_keys": keys}), 200


@rbac_bp.route("/v1/api-keys/<key_id>", methods=["DELETE"])
@require_auth
def revoke_api_key(key_id):
    """Revoke an API key belonging to the current user."""
    _store().revoke_api_key(key_id, user_id=g.current_user.id)
    return jsonify({"ok": True, "key_id": key_id, "status": "revoked"}), 200
