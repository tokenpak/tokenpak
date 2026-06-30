# SPDX-License-Identifier: Apache-2.0
"""tokenpak.platform — cross-platform process and service lifecycle adapters.

This package isolates OS-specific assumptions (systemd/cron/journalctl on Linux,
``pkill``/``ss``/``curl`` shellouts, POSIX session semantics, ``/proc`` reads) so
that product commands can run on Linux, macOS, and native Windows with honest
supported / degraded / unsupported behavior instead of missing-binary tracebacks.

Submodules:
  - :mod:`tokenpak.platform.process` — process lifecycle (liveness, termination,
    detached background start, port checks).
  - :mod:`tokenpak.platform.service` — managed-service / scheduler operations
    (proxy service restart, log retrieval, crontab/at scheduling) with
    platform-appropriate availability results.

Nothing here is part of the released public API surface; ``__all__`` is empty so
the package contributes no symbols to the public-API snapshot. Consumers import
the *modules* (``from tokenpak.platform import process, service``) and call
through the module qualifier.
"""

from __future__ import annotations

__all__: list[str] = []
