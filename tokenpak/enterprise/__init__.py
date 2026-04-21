"""DEPRECATED — `tokenpak.enterprise` moved to `tokenpak_paid.enterprise` (TPS-11).

The canonical home is ``tokenpak_paid.enterprise`` (shipped with
an Enterprise subscription). This namespace is kept for backwards
compatibility with the DeprecationWarning-shim policy (removal
target: tokenpak 2.0).
"""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.enterprise: moved to tokenpak_paid.enterprise (Enterprise tier). "
    "Install with `tokenpak install-tier enterprise`. "
    "This OSS shim will be removed in tokenpak 2.0.",
    DeprecationWarning,
    stacklevel=2,
)
