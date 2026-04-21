"""Core config subpackage (Architecture §1).

Config loading + validation. Split into submodules:
    loader    — reads the canonical config chain (defaults → user → project → env → CLI)
    validator — validates config shape against the declared schema
"""

from __future__ import annotations

from tokenpak.core.config.loader import *  # noqa: F401,F403
from tokenpak.core.config.validator import *  # noqa: F401,F403
