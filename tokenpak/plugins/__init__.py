"""Plugins subsystem (Architecture §1).

Extension without core bloat. Hook points, optional plugin modules,
plugin discovery + loading, example plugins. Level-4 per §2.

Hooks surface at ``services/`` pipeline stages and at ``proxy/``
middleware boundaries; plugins consume but never duplicate pipeline
logic (Architecture §1.4 plane rule 3).

Namespace init.
"""

from __future__ import annotations
