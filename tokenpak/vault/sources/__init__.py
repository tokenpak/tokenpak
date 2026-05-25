"""Vault source adapters — additive content sources for the BM25 index.

Adapters write into the same ``~/vault/.tokenpak/index.json`` + ``blocks/``
store consumed by ``tokenpak.proxy.vault_bridge.VaultIndex``. Each adapter
tags its blocks with a distinct ``source_type`` so callers can label hits
separately from filesystem-vault docs.

Phase 0 / OSS adapters:

* ``claude_transcript`` — index this host's Claude Code session transcripts
  under ``~/.claude/projects/*/*.jsonl``.  Off by default; enable with
  ``TOKENPAK_INDEX_CLAUDE_TRANSCRIPTS=1``.
"""
