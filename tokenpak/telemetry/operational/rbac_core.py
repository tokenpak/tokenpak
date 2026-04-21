"""
TokenPak RBAC Core — Role definitions, permission matrix, and access control.

Originally authored in the internal ops repo; this is the canonical copy
shipped with tokenpak.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Set


class Role(Enum):
    """Available roles in TokenPak."""

    ADMIN = "admin"
    FINOPS = "finops"
    ENGINEER = "engineer"
    AUDITOR = "auditor"
    READONLY = "readonly"


class Permission(Enum):
    """Permission identifiers."""

    # Dashboard
    VIEW_DASHBOARD = "view_dashboard"
    VIEW_COST = "view_cost"
    VIEW_SEGMENTS = "view_segments"
    VIEW_PAYLOAD = "view_payload"

    # Export
    EXPORT_COST = "export_cost"
    EXPORT_TRACE = "export_trace"

    # Configuration
    MODIFY_PRICING = "modify_pricing"
    MODIFY_RETENTION = "modify_retention"
    ENABLE_DEBUG = "enable_debug"

    # User management
    MANAGE_USERS = "manage_users"
    VIEW_SETTINGS = "view_settings"
    MODIFY_SETTINGS = "modify_settings"

    # Data operations
    REPROCESS_DATA = "reprocess_data"
    VIEW_AUDIT_LOG = "view_audit_log"


# Permission matrix: Role -> Set of Permissions
PERMISSION_MATRIX: dict[Role, Set[Permission]] = {
    Role.ADMIN: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_COST,
        Permission.VIEW_SEGMENTS,
        Permission.VIEW_PAYLOAD,
        Permission.EXPORT_COST,
        Permission.EXPORT_TRACE,
        Permission.MODIFY_PRICING,
        Permission.MODIFY_RETENTION,
        Permission.ENABLE_DEBUG,
        Permission.MANAGE_USERS,
        Permission.VIEW_SETTINGS,
        Permission.MODIFY_SETTINGS,
        Permission.REPROCESS_DATA,
        Permission.VIEW_AUDIT_LOG,
    },
    Role.FINOPS: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_COST,
        Permission.EXPORT_COST,
        Permission.VIEW_SETTINGS,
    },
    Role.ENGINEER: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_COST,
        Permission.VIEW_SEGMENTS,
        Permission.VIEW_PAYLOAD,
        Permission.EXPORT_TRACE,
        Permission.ENABLE_DEBUG,
        Permission.VIEW_SETTINGS,
        Permission.VIEW_AUDIT_LOG,
    },
    Role.AUDITOR: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_COST,
        Permission.VIEW_SEGMENTS,
        Permission.VIEW_PAYLOAD,
        Permission.EXPORT_COST,
        Permission.EXPORT_TRACE,
        Permission.VIEW_SETTINGS,
        Permission.VIEW_AUDIT_LOG,
    },
    Role.READONLY: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_COST,
        Permission.VIEW_SETTINGS,
    },
}


@dataclass
class User:
    """User with role and settings."""

    id: str
    username: str
    role: Role
    created_at: datetime
    last_login: Optional[datetime] = None
    settings: dict = field(default_factory=dict)
    is_active: bool = True

    def has_permission(self, permission: Permission) -> bool:
        """Check if user has permission."""
        allowed = PERMISSION_MATRIX.get(self.role, set())
        return permission in allowed

    def has_any_permission(self, *permissions: Permission) -> bool:
        """Check if user has any of the permissions."""
        return any(self.has_permission(p) for p in permissions)

    def has_all_permissions(self, *permissions: Permission) -> bool:
        """Check if user has all permissions."""
        return all(self.has_permission(p) for p in permissions)


class AccessControl:
    """Static helpers for permission checks."""

    @staticmethod
    def user_can(user: Optional[User], permission: Permission) -> bool:
        if user is None:
            return False
        return user.has_permission(permission)

    @staticmethod
    def get_allowed_permissions(role: Role) -> Set[Permission]:
        return PERMISSION_MATRIX.get(role, set()).copy()

    @staticmethod
    def get_role_description(role: Role) -> str:
        descriptions = {
            Role.ADMIN: "Administrator — full access to all features",
            Role.FINOPS: "FinOps — financial operations and cost analysis",
            Role.ENGINEER: "Engineer — development and debugging access",
            Role.AUDITOR: "Auditor — audit and compliance monitoring",
            Role.READONLY: "Read-Only — view-only access",
        }
        return descriptions.get(role, "Unknown role")
