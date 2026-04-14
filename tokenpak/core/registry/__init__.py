"""TokenPak registry — installable adapter packages.

Re-exports Block and BlockRegistry from the flat registry.py module so that
`from tokenpak.core.registry import Block, BlockRegistry` continues to work
even though this package directory shadows the flat registry.py module.
"""

# The registry/ package directory shadows registry.py — re-export the
# core dataclasses so existing imports in calibration.py etc. still work.
import importlib.util as _ilu
import os as _os

_flat_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "registry.py")
_spec = _ilu.spec_from_file_location("tokenpak._registry_flat", _flat_path)
_flat = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_flat)  # type: ignore[union-attr]

Block = _flat.Block  # noqa: F401
BlockRegistry = _flat.BlockRegistry  # noqa: F401
