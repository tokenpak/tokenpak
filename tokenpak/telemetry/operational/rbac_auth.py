"""
TokenPak RBAC Auth — User management, authentication, and API key handling.

Provides:
  - SQLite-backed user store (tp_users, tp_api_keys, tp_sessions, tp_audit_log)
  - Session token creation / validation (opaque random tokens, no JWT dependency)
  - API key creation / validation
  - First-run admin bootstrap
  - Flask request integration (load_user_from_request / g.current_user)
  - Flask decorators: require_auth, require_permission
"""

import hashlib
import logging
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable, Optional

from flask import g, jsonify, request

from .rbac_core import Permission, Role, User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_TTL_HOURS = 24
API_KEY_PREFIX = "tp_"
ADMIN_BOOTSTRAP_ENV = "TOKENPAK_ADMIN_BOOTSTRAP"  # set to "1" to force re-bootstrap

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tp_users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    role TEXT NOT NULL DEFAULT 'readonly',
    email TEXT UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login DATETIME,
    is_active BOOLEAN DEFAULT TRUE,
    settings JSON DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tp_api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES tp_users(id) ON DELETE CASCADE,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    name TEXT,
    role TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_used DATETIME,
    expires_at DATETIME,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS tp_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT REFERENCES tp_users(id),
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    changes JSON,
    ip_address TEXT,
    status TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    details TEXT
);

CREATE TABLE IF NOT EXISTS tp_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES tp_users(id) ON DELETE CASCADE,
    token TEXT UNIQUE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,
    ip_address TEXT,
    user_agent TEXT,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_users_username  ON tp_users(username);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash   ON tp_api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_token  ON tp_sessions(token);
CREATE INDEX IF NOT EXISTS idx_audit_created   ON tp_audit_log(created_at);
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_password(password: str) -> str:
    """Hash a password using sha256 + salt (simple, no bcrypt dependency)."""
    salt = secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{digest}"


def _verify_password(password: str, stored_hash: str) -> bool:
    parts = stored_hash.split(":", 1)
    if len(parts) != 2:
        return False
    salt, digest = parts
    expected = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return secrets.compare_digest(expected, digest)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        role=Role(row["role"]),
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else _now_utc(),
        last_login=datetime.fromisoformat(row["last_login"]) if row["last_login"] else None,
        is_active=bool(row["is_active"]),
    )


# ---------------------------------------------------------------------------
# RBACStore
# ---------------------------------------------------------------------------


class RBACStore:
    """SQLite-backed store for users, sessions, and API keys."""

    def __init__(self, db_path: str):
        self.db_path = os.path.expanduser(db_path)
        self._init_db()
        self._maybe_bootstrap_admin()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _maybe_bootstrap_admin(self):
        """Create default admin on first run if no users exist."""
        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM tp_users").fetchone()[0]
            force = os.environ.get(ADMIN_BOOTSTRAP_ENV) == "1"
            if count == 0 or force:
                password = secrets.token_urlsafe(16)
                user_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT OR IGNORE INTO tp_users
                       (id, username, password_hash, role, created_at, is_active)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (user_id, "admin", _hash_password(password), Role.ADMIN.value,
                     _now_utc().isoformat(), True),
                )
                print(f"\n{'='*50}")
                print("TokenPak RBAC — First-run admin account created")
                print(f"  Username : admin")
                print(f"  Password : {password}")
                print("  Change this password immediately!")
                print(f"{'='*50}\n")
                logger.warning("Admin bootstrapped — change the default password.")

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def create_user(self, username: str, password: str, role: Role,
                    email: Optional[str] = None, created_by_id: Optional[str] = None) -> User:
        user_id = str(uuid.uuid4())
        now = _now_utc().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO tp_users (id, username, password_hash, role, email, created_at, updated_at, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, _hash_password(password), role.value, email, now, now, True),
            )
            self._audit(conn, created_by_id, "user.create", "user", user_id, status="ok")
        return self.get_user_by_id(user_id)

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tp_users WHERE id=?", (user_id,)).fetchone()
            return _row_to_user(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[User]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tp_users WHERE username=?", (username,)).fetchone()
            return _row_to_user(row) if row else None

    def list_users(self, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, username, role, email, created_at, last_login, is_active
                   FROM tp_users ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM tp_users").fetchone()[0]
        return [dict(r) for r in rows], total

    def update_user(self, user_id: str, *, role: Optional[str] = None,
                    email: Optional[str] = None, is_active: Optional[bool] = None,
                    updated_by_id: Optional[str] = None) -> Optional[User]:
        with self._conn() as conn:
            if role is not None:
                conn.execute("UPDATE tp_users SET role=?, updated_at=? WHERE id=?",
                             (role, _now_utc().isoformat(), user_id))
            if email is not None:
                conn.execute("UPDATE tp_users SET email=?, updated_at=? WHERE id=?",
                             (email, _now_utc().isoformat(), user_id))
            if is_active is not None:
                conn.execute("UPDATE tp_users SET is_active=?, updated_at=? WHERE id=?",
                             (is_active, _now_utc().isoformat(), user_id))
            self._audit(conn, updated_by_id, "user.update", "user", user_id, status="ok")
        return self.get_user_by_id(user_id)

    def deactivate_user(self, user_id: str, deactivated_by_id: Optional[str] = None) -> bool:
        with self._conn() as conn:
            conn.execute("UPDATE tp_users SET is_active=FALSE, updated_at=? WHERE id=?",
                         (_now_utc().isoformat(), user_id))
            self._audit(conn, deactivated_by_id, "user.deactivate", "user", user_id, status="ok")
        return True

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self, username: str, password: str) -> Optional[User]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tp_users WHERE username=? AND is_active=TRUE", (username,)
            ).fetchone()
            if row is None:
                return None
            if not _verify_password(password, row["password_hash"]):
                return None
            conn.execute("UPDATE tp_users SET last_login=? WHERE id=?",
                         (_now_utc().isoformat(), row["id"]))
            return _row_to_user(row)

    def create_session(self, user: User, ip: Optional[str] = None,
                       user_agent: Optional[str] = None) -> str:
        token = secrets.token_urlsafe(32)
        session_id = str(uuid.uuid4())
        expires_at = (_now_utc() + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO tp_sessions (id, user_id, token, created_at, expires_at, ip_address, user_agent, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, user.id, _hash_token(token),
                 _now_utc().isoformat(), expires_at, ip, user_agent, True),
            )
        return token

    def validate_session(self, token: str) -> Optional[User]:
        token_hash = _hash_token(token)
        with self._conn() as conn:
            row = conn.execute(
                """SELECT s.user_id, s.expires_at
                   FROM tp_sessions s
                   WHERE s.token=? AND s.is_active=TRUE""",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at < _now_utc():
                conn.execute("UPDATE tp_sessions SET is_active=FALSE WHERE token=?", (token_hash,))
                return None
            user_row = conn.execute(
                "SELECT * FROM tp_users WHERE id=? AND is_active=TRUE", (row["user_id"],)
            ).fetchone()
            return _row_to_user(user_row) if user_row else None

    def invalidate_session(self, token: str) -> bool:
        token_hash = _hash_token(token)
        with self._conn() as conn:
            conn.execute("UPDATE tp_sessions SET is_active=FALSE WHERE token=?", (token_hash,))
        return True

    # ------------------------------------------------------------------
    # API Keys
    # ------------------------------------------------------------------

    def create_api_key(self, user: User, name: str = "",
                       role: Optional[Role] = None,
                       expires_in_days: Optional[int] = None) -> tuple[str, dict]:
        """Returns (raw_key, key_record). Store raw_key — it won't be shown again."""
        raw_key = API_KEY_PREFIX + secrets.token_urlsafe(32)
        key_id = str(uuid.uuid4())
        key_prefix = raw_key[:10]
        assigned_role = (role or user.role).value
        expires_at = None
        if expires_in_days:
            expires_at = (_now_utc() + timedelta(days=expires_in_days)).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO tp_api_keys
                   (id, user_id, key_hash, key_prefix, name, role, created_at, expires_at, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key_id, user.id, _hash_token(raw_key), key_prefix,
                 name, assigned_role, _now_utc().isoformat(), expires_at, True),
            )
        record = {
            "id": key_id,
            "prefix": key_prefix,
            "name": name,
            "role": assigned_role,
            "created_at": _now_utc().isoformat(),
            "expires_at": expires_at,
        }
        return raw_key, record

    def validate_api_key(self, raw_key: str) -> Optional[User]:
        key_hash = _hash_token(raw_key)
        with self._conn() as conn:
            row = conn.execute(
                """SELECT k.user_id, k.role, k.expires_at, k.id
                   FROM tp_api_keys k
                   WHERE k.key_hash=? AND k.is_active=TRUE""",
                (key_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"]:
                exp = datetime.fromisoformat(row["expires_at"])
                if exp < _now_utc():
                    conn.execute("UPDATE tp_api_keys SET is_active=FALSE WHERE id=?", (row["id"],))
                    return None
            conn.execute("UPDATE tp_api_keys SET last_used=? WHERE id=?",
                         (_now_utc().isoformat(), row["id"]))
            user_row = conn.execute(
                "SELECT * FROM tp_users WHERE id=? AND is_active=TRUE", (row["user_id"],)
            ).fetchone()
            if user_row is None:
                return None
            # API key may override user role
            user = _row_to_user(user_row)
            try:
                user.role = Role(row["role"])
            except ValueError:
                pass
            return user

    def list_api_keys(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, key_prefix, name, role, created_at, last_used, expires_at, is_active
                   FROM tp_api_keys WHERE user_id=? ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: str, user_id: str) -> bool:
        with self._conn() as conn:
            conn.execute(
                "UPDATE tp_api_keys SET is_active=FALSE WHERE id=? AND user_id=?",
                (key_id, user_id),
            )
        return True

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def _audit(self, conn: sqlite3.Connection, user_id: Optional[str], action: str,
               resource_type: Optional[str] = None, resource_id: Optional[str] = None,
               status: str = "ok", details: Optional[str] = None):
        conn.execute(
            """INSERT INTO tp_audit_log
               (user_id, action, resource_type, resource_id, status, created_at, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, action, resource_type, resource_id, status, _now_utc().isoformat(), details),
        )

    def get_audit_log(self, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT al.*, u.username FROM tp_audit_log al
                   LEFT JOIN tp_users u ON al.user_id = u.id
                   ORDER BY al.created_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Flask Integration
# ---------------------------------------------------------------------------


def load_user_from_request(store: RBACStore) -> Optional[User]:
    """
    Extract and validate caller identity from the current Flask request.

    Priority order:
      1. Bearer token (Authorization: Bearer <token>)
      2. X-API-Key header
      3. api_key query parameter
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return store.validate_session(token)

    api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
    if api_key:
        return store.validate_api_key(api_key)

    return None


def init_rbac(app, db_path: str) -> RBACStore:
    """
    Register RBAC store on a Flask app.

    Usage::

        store = init_rbac(app, "~/.openclaw/workspace/.ocp/rbac.db")
    """
    store = RBACStore(db_path)

    @app.before_request
    def _load_user():
        g.current_user = load_user_from_request(store)

    # Attach store for use in route handlers
    app.rbac_store = store
    return store


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def require_auth(f: Callable) -> Callable:
    """Require an authenticated caller (401 if missing)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.get("current_user") is None:
            return jsonify({"error": "Unauthorized", "message": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def require_permission(*permissions: Permission) -> Callable:
    """Require at least one of the listed permissions (403 if denied)."""
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated(*args, **kwargs):
            user: Optional[User] = g.get("current_user")
            if user is None:
                return jsonify({"error": "Unauthorized"}), 401
            if not user.has_any_permission(*permissions):
                return jsonify({
                    "error": "Forbidden",
                    "required_permissions": [p.value for p in permissions],
                    "your_role": user.role.value,
                }), 403
            return f(*args, **kwargs)
        return decorated
    return decorator
