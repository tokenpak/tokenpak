# SPDX-License-Identifier: Apache-2.0
"""TokenPak configuration helpers.

This package holds configuration-resolution helpers and the canonical
config schema (``schema.json``). The :mod:`tokenpak.config.load_order`
module documents and implements the environment-variable resolution
*specification* as a pure, importable helper; it is intentionally NOT
wired into the live proxy/daemon startup path.
"""
