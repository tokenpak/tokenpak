"""tokenpak.agent.agentic.proxy_workflow — ProxyWorkflowAdapter

Wires the WorkflowManager into the TokenPak proxy pipeline.

Feature flag: TOKENPAK_WORKFLOW_TRACKING (default OFF)
  - 0 / unset  → completely disabled; zero overhead
  - 1           → each proxy request gets a workflow record persisted to disk

All public functions are safe to call unconditionally — they are no-ops
when the feature flag is disabled, so the proxy never needs to branch on
the flag itself.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------
WORKFLOW_TRACKING_ENABLED: bool = os.environ.get(
    "TOKENPAK_WORKFLOW_TRACKING", "0"
).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Lazy import guard — only pull in WorkflowManager when tracking is ON
# ---------------------------------------------------------------------------
def _get_manager():
    """Return a singleton WorkflowManager, or None when tracking is disabled."""
    if not WORKFLOW_TRACKING_ENABLED:
        return None
    from tokenpak.agent.agentic.workflow import get_manager

    return get_manager()


# ---------------------------------------------------------------------------
# Public API used by proxy.py
# ---------------------------------------------------------------------------


def start_proxy_workflow(
    request_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Create and start a proxy workflow for *request_id*.

    Returns the workflow ID (same as request_id) when tracking is enabled,
    or None when the feature flag is OFF.

    Steps: vault_inject → compress → forward → log_metrics
    Tags:  proxy, request_id
    """
    mgr = _get_manager()
    if mgr is None:
        return None

    try:
        from tokenpak.agent.agentic.workflow import template_steps

        steps = template_steps("proxy")
        wf = mgr.create(
            name=f"proxy-request-{request_id}",
            steps=steps,
            template="proxy",
            metadata={"request_id": request_id, **(metadata or {})},
            tags=["proxy", request_id],
            wf_id=request_id,
        )
        mgr.start(wf.id)
        mgr.begin_step(wf.id, "vault_inject")
        return wf.id
    except Exception as exc:
        logger.debug("proxy_workflow.start_proxy_workflow failed (non-fatal): %s", exc)
        return None


def advance_step(
    wf_id: str,
    complete_step_name: str,
    begin_step_name: Optional[str] = None,
) -> None:
    """Mark *complete_step_name* as completed and optionally begin *begin_step_name*.

    Safe to call even when *wf_id* is None or tracking is disabled.
    """
    if wf_id is None:
        return
    mgr = _get_manager()
    if mgr is None:
        return

    try:
        mgr.complete_step(wf_id, complete_step_name)
        if begin_step_name:
            mgr.begin_step(wf_id, begin_step_name)
    except Exception as exc:
        logger.debug("proxy_workflow.advance_step failed (non-fatal): %s", exc)


def complete_workflow(wf_id: Optional[str]) -> None:
    """Complete the *log_metrics* step and close the workflow as COMPLETED.

    No-op when wf_id is None or tracking is disabled.
    """
    if wf_id is None:
        return
    mgr = _get_manager()
    if mgr is None:
        return

    try:
        mgr.complete_step(wf_id, "log_metrics")
        # _maybe_close() fires automatically inside complete_step when all steps are terminal
    except Exception as exc:
        logger.debug("proxy_workflow.complete_workflow failed (non-fatal): %s", exc)


def fail_step(
    wf_id: Optional[str],
    step_name: str,
    error: str = "",
) -> None:
    """Mark *step_name* as FAILED without crashing the proxy response.

    Remaining dependent steps are skipped automatically.
    The workflow is marked FAILED but the proxy continues normally.
    """
    if wf_id is None:
        return
    mgr = _get_manager()
    if mgr is None:
        return

    try:
        mgr.fail_step(wf_id, step_name, error=error or "unspecified error")
    except Exception as exc:
        logger.debug("proxy_workflow.fail_step failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Recovery helpers
# ---------------------------------------------------------------------------


def recover_proxy_workflows() -> List[Dict[str, Any]]:
    """Return a list of incomplete proxy workflows from prior runs.

    Used by the proxy on startup to surface dangling state.
    Returns [] when tracking is disabled or no incomplete workflows exist.
    """
    mgr = _get_manager()
    if mgr is None:
        return []

    try:
        incomplete = mgr.incomplete_workflows()
        proxy_incomplete = [wf for wf in incomplete if "proxy" in wf.tags]
        return [wf.to_dict() for wf in proxy_incomplete]
    except Exception as exc:
        logger.debug("proxy_workflow.recover_proxy_workflows failed (non-fatal): %s", exc)
        return []
