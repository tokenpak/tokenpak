"""Canonical TIP metadata fields.

Runtime metadata attached to requests, responses, and telemetry events.
Schemas are the source of truth in the registry repo
(``schemas/tip/metadata.schema.json``); this module exposes the
reference Python types.

Phase 1 scaffold. Phase 2 populates dataclasses mirroring the JSON
schema and the validation helpers used by ``services/`` and
``telemetry/``.
"""

from __future__ import annotations
