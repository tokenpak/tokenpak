"""TokenPak Team — shared vault, agent registry, and team templates."""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.team is deprecated, use tokenpak._internal.team instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from .agent_registry import AgentRecord, AgentRegistry, get_agent_registry
from .shared_vault import SharedVault, SharedVaultBlock, get_shared_vault
from .templates import Template, TemplateStore, get_template_store

__all__ = [
    "AgentRecord",
    "AgentRegistry",
    "get_agent_registry",
    "SharedVaultBlock",
    "SharedVault",
    "get_shared_vault",
    "Template",
    "TemplateStore",
    "get_template_store",
]
