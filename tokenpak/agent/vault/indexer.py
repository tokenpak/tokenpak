"""TokenPak Agent Vault Indexer — index local files into compressed block storage."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Callable, Optional

from tokenpak.agent.ingest.schema_converter import convert_document
from tokenpak.extraction import EntityExtractor
from tokenpak.processors import get_processor
from tokenpak.tokens import count_tokens
from tokenpak.walker import detect_file_type, walk_directory

from .blocks import BlockRecord, BlockStore, SliceStore, get_block_store
from .slicer import SliceRecord, should_slice, slice_content
from .symbols import SymbolTable


class VaultIndexer:
    """Index a directory of code and doc files into compressed block storage.

    Usage::

        indexer = VaultIndexer()
        results = indexer.index_directory("~/projects/myapp")
        print(f"Indexed {results['files_indexed']} files")

        # Search indexed content
        blocks = indexer.search("authentication middleware")
    """

    def __init__(
        self,
        block_store: Optional[BlockStore] = None,
        symbol_table: Optional[SymbolTable] = None,
        slice_store: Optional[SliceStore] = None,
    ):
        self.blocks = block_store if block_store is not None else get_block_store()
        self.symbols = symbol_table if symbol_table is not None else SymbolTable()
        self.slices = slice_store if slice_store is not None else SliceStore(":memory:")
        self.extractor = EntityExtractor()

    def index_file(self, path: str, content: Optional[str] = None) -> Optional[BlockRecord]:
        """Index a single file. Reads from disk if content not provided."""
        file_path = Path(path)
        if not file_path.exists() and content is None:
            return None

        if content is None:
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return None

        # Incremental check: skip if content hasn't changed
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        existing = self.blocks.get_by_path(path)
        if existing and existing[0].content_hash == content_hash:
            return existing[0]  # Already indexed, no change

        # Detect file type via extension or basename (e.g. ".env")
        file_type = detect_file_type(path)
        if file_type not in ("code", "text", "data"):
            return None

        # Compress
        processor = get_processor(file_type)
        if processor is None:
            return None

        compressed = processor.process(content, path)
        raw_tokens = count_tokens(content)
        compressed_tokens = count_tokens(compressed)
        block_id = f"{path}#{content_hash[:8]}"

        schema_info = convert_document(content, filename=path)
        extracted = self.extractor.extract(content)
        compact_entities = self.extractor.compact_text(extracted)

        record = BlockRecord(
            block_id=block_id,
            path=path,
            content_hash=content_hash,
            file_type=file_type,
            raw_tokens=raw_tokens,
            compressed_tokens=compressed_tokens,
            compressed_content=compressed,
            metadata={
                "doc_type": schema_info.get("doc_type"),
                "schema": schema_info.get("schema"),
                "entities": extracted.to_compact_dict(),
                "entities_compact": compact_entities,
            },
        )
        self.blocks.save(record)

        # Semantic slicing for long structured text assets.
        # Remove stale slices for this parent block before re-slicing.
        self.slices.delete_by_parent(record.block_id)
        if should_slice(content, path):
            for sr in slice_content(content, record.block_id, path):
                self.slices.save(sr)

        # Index symbols for code, docs, and structured data.
        if file_type in ("code", "text", "data"):
            self.symbols.index_file(path, content)

        return record

    def index_directory(
        self,
        root: str,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Walk and index all supported files under root.

        Returns a summary dict::

            {
                "files_found": 42,
                "files_indexed": 40,
                "files_skipped": 2,
                "tokens_raw": 180000,
                "tokens_compressed": 95000,
                "tokens_saved": 85000,
                "duration_ms": 1234,
            }
        """
        start = time.time()
        files = walk_directory(root)

        indexed = skipped = raw_total = compressed_total = 0

        for path, _file_type, _size in files:
            record = self.index_file(path)
            if record:
                indexed += 1
                raw_total += record.raw_tokens
                compressed_total += record.compressed_tokens
                if on_progress:
                    on_progress(path)
            else:
                skipped += 1

        duration_ms = int((time.time() - start) * 1000)

        return {
            "files_found": len(files),
            "files_indexed": indexed,
            "files_skipped": skipped,
            "tokens_raw": raw_total,
            "tokens_compressed": compressed_total,
            "tokens_saved": max(0, raw_total - compressed_total),
            "duration_ms": duration_ms,
        }

    def search(self, query: str, top_k: int = 10) -> list[BlockRecord]:
        """Search indexed blocks by keyword."""
        return self.blocks.search(query, top_k=top_k)

    def search_slices(self, query: str, top_k: int = 10) -> list[SliceRecord]:
        """Search semantic sub-blocks (slices) by keyword.

        Returns the most relevant :class:`SliceRecord` objects for *query*,
        ranked by keyword frequency.  Use this for section-level retrieval
        from long structured documents.
        """
        return self.slices.search(query, top_k=top_k)

    def get_slices_for_file(self, path: str) -> list[SliceRecord]:
        """Return all slices for the given source file path, in document order."""
        return self.slices.get_by_path(path)

    def lookup_symbol(self, name: str):
        """Look up a symbol by exact name."""
        return self.symbols.lookup(name)

    def stats(self) -> dict:
        """Return indexer stats."""
        return {
            **self.blocks.stats(),
            "total_symbols": len(self.symbols),
        }

    def stats_by_type(self) -> dict:
        """Return indexed file count broken down by file type and extension."""
        from collections import Counter

        blocks = self.blocks.all()
        by_type: Counter = Counter()
        by_ext: Counter = Counter()
        for b in blocks:
            by_type[b.file_type] += 1
            ext = Path(b.path).suffix.lower() or "(no ext)"
            by_ext[ext] += 1
        return {
            "total_files": len(blocks),
            "by_type": dict(by_type),
            "by_extension": dict(sorted(by_ext.items())),
        }
