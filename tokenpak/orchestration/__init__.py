"""Multi-step and multi-agent coordination subsystem (Architecture §1).

Workflow engine, handoffs, agent/capabilities registry, workflow state
+ persistence. Level-4 per §2 dependency tiering. Composes multi-step
flows on top of ``tokenpak.services``; per-step execution is always
delegated to ``services.execute``.

This is a namespace package init — actual code lives in the existing
subdirectories (``agents/``, ``workflow/``, ``state_collector.py``,
etc.) per the D1 migration roadmap in Architecture §10.
"""

from __future__ import annotations
