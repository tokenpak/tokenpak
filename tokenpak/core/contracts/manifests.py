"""TIP manifest schemas + validators.

Manifests declare what a TokenPak-adjacent component is (adapter,
plugin, provider profile, client profile) — its id, version,
capabilities, compatibility ranges, trust metadata. JSON schemas live
in the registry repo at ``schemas/manifests/``; this module exposes
loaders and validators that resolve the schemas at runtime.

Phase 1 scaffold. Phase 2 populates the manifest parser, the
registry-backed schema fetch, and the per-kind validators used by the
CLI ``integrate`` command and by ``services/`` discovery.
"""

from __future__ import annotations
