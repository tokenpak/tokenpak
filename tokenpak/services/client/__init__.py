"""Internal client helpers for services callers.

Not to be confused with ``tokenpak.proxy.client`` — that is the
Architecture §2.4 contract surface entrypoints use. This package holds
internal helpers specific to services implementations (request-id
generation, profile detection, lightweight context objects) that don't
belong in the public ``services`` API and don't belong in any primitive.

Phase 2 scaffold.
"""

from __future__ import annotations
