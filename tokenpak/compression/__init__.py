"""Compression subsystem (Architecture §1).

End-to-end compression pipeline: segmentation, fingerprinting,
strategies by content type, budgets, fidelity tiers, canon blocks,
query rewriting, output formatting. Level-1 primitive per §2.
Invoked by ``tokenpak.services.compression_service``; never imported
directly by entrypoints for request execution.

Namespace init — actual code lives in ``engines/``, ``extraction/``,
``fingerprinting/``, ``processors/``, ``salience/`` subdirectories
per the D1 migration roadmap.
"""

from __future__ import annotations
