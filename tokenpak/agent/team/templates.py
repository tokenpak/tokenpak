"""TokenPak Team Templates (5.10)

Shared prompts, RBAC-controlled.
- Admins can create/delete templates.
- All team members can list and use templates.

CLI surface:
    tokenpak template list --team           — list team templates
    tokenpak template use <name> --team     — print/use a team template
    tokenpak template create <name>         — create template (admin only)
    tokenpak template delete <name>         — delete template (admin only)
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Roles
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"


@dataclass
class Template:
    """A shared team prompt template."""

    name: str
    content: str
    created_by: str  # agent/user name
    role_required: str = ROLE_MEMBER  # minimum role to use
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    description: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Template":
        return cls(**data)

    def render(self, variables: Optional[Dict[str, str]] = None) -> str:
        """Render template with optional variable substitution ({{var}} syntax)."""
        content = self.content
        if variables:
            for key, value in variables.items():
                content = content.replace(f"{{{{{key}}}}}", value)
        return content


class TemplateStore:
    """JSON-backed store for team templates with RBAC.

    Usage::

        store = TemplateStore("~/.tokenpak/team/templates.json")
        store.create("summarise", "Summarise this: {{content}}", created_by="admin", actor_role="admin")
        templates = store.list_templates()
        template = store.get("summarise")
        rendered = template.render({"content": "..."})
    """

    def __init__(self, store_path: str = ":memory:") -> None:
        self._path = store_path
        self._templates: Dict[str, Template] = {}
        self._lock = threading.Lock()

        if store_path != ":memory:":
            self._load()

    # ------------------------------------------------------------------
    # RBAC helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_admin(actor_role: str) -> None:
        if actor_role != ROLE_ADMIN:
            raise PermissionError(f"Only admins can perform this action (role: {actor_role!r})")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        content: str,
        created_by: str,
        actor_role: str = ROLE_ADMIN,
        description: str = "",
        tags: Optional[List[str]] = None,
        role_required: str = ROLE_MEMBER,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Template:
        """Create a new template (admin only)."""
        self._require_admin(actor_role)
        with self._lock:
            if name in self._templates:
                raise ValueError(f"Template {name!r} already exists. Use update() to modify.")
            template = Template(
                name=name,
                content=content,
                created_by=created_by,
                role_required=role_required,
                description=description,
                tags=tags or [],
                metadata=metadata or {},
            )
            self._templates[name] = template
            self._persist()
            return template

    def update(
        self,
        name: str,
        content: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        actor_role: str = ROLE_ADMIN,
    ) -> Template:
        """Update an existing template (admin only)."""
        self._require_admin(actor_role)
        with self._lock:
            if name not in self._templates:
                raise KeyError(f"Template {name!r} not found.")
            template = self._templates[name]
            if content is not None:
                template.content = content
            if description is not None:
                template.description = description
            if tags is not None:
                template.tags = tags
            template.updated_at = time.time()
            self._persist()
            return template

    def delete(self, name: str, actor_role: str = ROLE_ADMIN) -> bool:
        """Delete a template (admin only)."""
        self._require_admin(actor_role)
        with self._lock:
            if name not in self._templates:
                return False
            del self._templates[name]
            self._persist()
            return True

    def get(self, name: str, actor_role: str = ROLE_MEMBER) -> Optional[Template]:
        """Retrieve a template by name (any team member)."""
        with self._lock:
            template = self._templates.get(name)
        if template is None:
            return None
        # Check RBAC
        if template.role_required == ROLE_ADMIN and actor_role != ROLE_ADMIN:
            raise PermissionError(f"Template {name!r} requires admin role.")
        return template

    def list_templates(
        self, actor_role: str = ROLE_MEMBER, tag: Optional[str] = None
    ) -> List[Template]:
        """List templates visible to actor (respects role_required)."""
        with self._lock:
            templates = list(self._templates.values())

        visible = []
        for t in templates:
            if t.role_required == ROLE_ADMIN and actor_role != ROLE_ADMIN:
                continue
            if tag and tag not in t.tags:
                continue
            visible.append(t)
        return sorted(visible, key=lambda t: t.name)

    def use(
        self,
        name: str,
        variables: Optional[Dict[str, str]] = None,
        actor_role: str = ROLE_MEMBER,
    ) -> str:
        """Fetch a template and render it with optional variables."""
        template = self.get(name, actor_role=actor_role)
        if template is None:
            raise KeyError(f"Template {name!r} not found.")
        return template.render(variables)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        if self._path == ":memory:":
            return
        path = Path(self._path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: t.to_dict() for name, t in self._templates.items()}
        path.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        path = Path(self._path).expanduser()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._templates = {name: Template.from_dict(td) for name, td in data.items()}
        except (json.JSONDecodeError, KeyError, TypeError):
            self._templates = {}

    def __len__(self) -> int:
        return len(self._templates)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            templates = list(self._templates.values())
        return {
            "total": len(templates),
            "admin_only": sum(1 for t in templates if t.role_required == ROLE_ADMIN),
            "all_members": sum(1 for t in templates if t.role_required == ROLE_MEMBER),
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_template_store: Optional[TemplateStore] = None
_store_lock = threading.Lock()


def get_template_store(store_path: str = ":memory:") -> TemplateStore:
    """Return the process-level singleton template store."""
    global _template_store
    with _store_lock:
        if _template_store is None:
            _template_store = TemplateStore(store_path)
    return _template_store
