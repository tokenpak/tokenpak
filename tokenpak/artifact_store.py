# SPDX-License-Identifier: Apache-2.0
"""Artifact store for TokenPak dynamic context."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .schemas.artifact import ArtifactSchema
from .schemas.chunk import ChunkSchema
from .schemas.retrieval_cache import RetrievalCacheSchema
from .schemas.source_map import SourceMapSchema


class ArtifactStore:
    """Store and retrieve artifacts for dynamic context."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize artifact store.

        Args:
            db_path: Path to SQLite database (default: ~/.tokenpak/artifacts.db)
        """
        if db_path is None:
            db_path = str(Path.home() / ".tokenpak" / "artifacts.db")

        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database schema."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Artifacts table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                origin TEXT,
                kind TEXT,
                content_ref TEXT,
                repo_binding TEXT,
                size_bytes INTEGER,
                token_estimate INTEGER,
                labels TEXT,
                created_at TEXT,
                accessed_at TEXT,
                stats TEXT
            )
        """
        )

        # Chunks table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                source TEXT,
                content TEXT,
                token_estimate INTEGER,
                symbols TEXT,
                embedding_ref TEXT,
                neighbors TEXT,
                metadata TEXT,
                created_at TEXT,
                stats TEXT
            )
        """
        )

        # Retrieval cache table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_cache (
                query_fingerprint TEXT PRIMARY KEY,
                session_id TEXT,
                repo_id TEXT,
                intent TEXT,
                results TEXT,
                coverage_score REAL,
                pack_plan TEXT,
                ttl_minutes INTEGER,
                created_at TEXT,
                last_used_at TEXT,
                use_count INTEGER,
                metadata TEXT
            )
        """
        )

        # Source map table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS source_maps (
                id TEXT PRIMARY KEY,
                repo_id TEXT,
                session_id TEXT,
                truth_preference TEXT,
                bindings TEXT,
                conflicts TEXT,
                metadata TEXT
            )
        """
        )

        conn.commit()
        conn.close()

    def _compute_hash(self, content: str) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content.encode()).hexdigest()

    def store_artifact(self, artifact: ArtifactSchema) -> str:
        """Store artifact in database. Returns artifact ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO artifacts
            (id, session_id, origin, kind, content_ref, repo_binding,
             size_bytes, token_estimate, labels, created_at, accessed_at, stats)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                artifact.id,
                artifact.session_id,
                artifact.origin,
                artifact.kind,
                artifact.content_ref,
                artifact.repo_binding,
                artifact.size_bytes,
                artifact.token_estimate,
                json.dumps(artifact.labels),
                artifact.created_at.isoformat(),
                artifact.accessed_at.isoformat(),
                json.dumps(artifact.stats),
            ),
        )

        conn.commit()
        conn.close()
        return artifact.id

    def retrieve_artifact(self, artifact_id: str) -> Optional[ArtifactSchema]:
        """Retrieve artifact by ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, session_id, origin, kind, content_ref, repo_binding,
                   size_bytes, token_estimate, labels, created_at, accessed_at, stats
            FROM artifacts
            WHERE id = ?
        """,
            (artifact_id,),
        )

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return ArtifactSchema(
            id=row[0],
            session_id=row[1],
            origin=row[2],
            kind=row[3],
            content_ref=row[4],
            repo_binding=row[5],
            size_bytes=row[6],
            token_estimate=row[7],
            labels=json.loads(row[8]),
            created_at=datetime.fromisoformat(row[9]),
            accessed_at=datetime.fromisoformat(row[10]),
            stats=json.loads(row[11]),
        )

    def store_chunk(self, chunk: ChunkSchema) -> str:
        """Store chunk in database. Returns chunk ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO chunks
            (id, source, content, token_estimate, symbols, embedding_ref,
             neighbors, metadata, created_at, stats)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                chunk.id,
                chunk.source,
                chunk.content,
                chunk.token_estimate,
                json.dumps(chunk.symbols),
                chunk.embedding_ref,
                json.dumps(chunk.neighbors),
                json.dumps(chunk.metadata),
                chunk.created_at.isoformat(),
                json.dumps(chunk.stats),
            ),
        )

        conn.commit()
        conn.close()
        return chunk.id

    def retrieve_chunk(self, chunk_id: str) -> Optional[ChunkSchema]:
        """Retrieve chunk by ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, source, content, token_estimate, symbols, embedding_ref,
                   neighbors, metadata, created_at, stats
            FROM chunks
            WHERE id = ?
        """,
            (chunk_id,),
        )

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return ChunkSchema(
            id=row[0],
            source=row[1],
            content=row[2],
            token_estimate=row[3],
            symbols=json.loads(row[4]),
            embedding_ref=row[5],
            neighbors=json.loads(row[6]),
            metadata=json.loads(row[7]),
            created_at=datetime.fromisoformat(row[8]),
            stats=json.loads(row[9]),
        )

    def get_chunk_neighbors(self, chunk_id: str) -> List[ChunkSchema]:
        """Get neighboring chunks."""
        chunk = self.retrieve_chunk(chunk_id)
        if not chunk:
            return []

        neighbors = []
        for neighbor_id in chunk.neighbors:
            neighbor = self.retrieve_chunk(neighbor_id)
            if neighbor:
                neighbors.append(neighbor)

        return neighbors

    def cache_retrieval_results(
        self, cache_entry: RetrievalCacheSchema
    ) -> None:
        """Store retrieval cache entry."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO retrieval_cache
            (query_fingerprint, session_id, repo_id, intent, results,
             coverage_score, pack_plan, ttl_minutes, created_at,
             last_used_at, use_count, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                cache_entry.query_fingerprint,
                cache_entry.session_id,
                cache_entry.repo_id,
                cache_entry.intent,
                json.dumps(cache_entry.results),
                cache_entry.coverage_score,
                json.dumps(cache_entry.pack_plan) if cache_entry.pack_plan else None,
                cache_entry.ttl_minutes,
                cache_entry.created_at.isoformat(),
                cache_entry.last_used_at.isoformat(),
                cache_entry.use_count,
                json.dumps(cache_entry.metadata),
            ),
        )

        conn.commit()
        conn.close()

    def get_cached_results(
        self, query_fingerprint: str
    ) -> Optional[RetrievalCacheSchema]:
        """Get retrieval cache entry. Returns None if expired."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT query_fingerprint, session_id, repo_id, intent, results,
                   coverage_score, pack_plan, ttl_minutes, created_at,
                   last_used_at, use_count, metadata
            FROM retrieval_cache
            WHERE query_fingerprint = ?
        """,
            (query_fingerprint,),
        )

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        cache_entry = RetrievalCacheSchema(
            query_fingerprint=row[0],
            session_id=row[1],
            repo_id=row[2],
            intent=row[3],
            results=json.loads(row[4]),
            coverage_score=row[5],
            pack_plan=json.loads(row[6]) if row[6] else None,
            ttl_minutes=row[7],
            created_at=datetime.fromisoformat(row[8]),
            last_used_at=datetime.fromisoformat(row[9]),
            use_count=row[10],
            metadata=json.loads(row[11]),
        )

        # Check if expired
        if cache_entry.is_expired():
            self.invalidate_cache_entry(query_fingerprint)
            return None

        return cache_entry

    def invalidate_cache_entry(self, query_fingerprint: str) -> None:
        """Remove cache entry."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM retrieval_cache WHERE query_fingerprint = ?",
                       (query_fingerprint,))

        conn.commit()
        conn.close()

    def invalidate_cache_by_repo(self, repo_id: str) -> None:
        """Invalidate all cache entries for a repo (on repo changes)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM retrieval_cache WHERE repo_id = ?", (repo_id,))

        conn.commit()
        conn.close()

    def store_source_map(self, source_map: SourceMapSchema) -> None:
        """Store source map."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        map_id = f"{source_map.repo_id}:{source_map.session_id}"

        cursor.execute(
            """
            INSERT OR REPLACE INTO source_maps
            (id, repo_id, session_id, truth_preference, bindings, conflicts, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                map_id,
                source_map.repo_id,
                source_map.session_id,
                source_map.truth_preference,
                json.dumps(source_map.bindings),
                json.dumps(source_map.conflicts),
                json.dumps(source_map.metadata),
            ),
        )

        conn.commit()
        conn.close()

    def get_source_map(
        self, repo_id: str, session_id: str
    ) -> Optional[SourceMapSchema]:
        """Get source map."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        map_id = f"{repo_id}:{session_id}"

        cursor.execute(
            """
            SELECT repo_id, session_id, truth_preference, bindings, conflicts, metadata
            FROM source_maps
            WHERE id = ?
        """,
            (map_id,),
        )

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return SourceMapSchema(
            repo_id=row[0],
            session_id=row[1],
            truth_preference=row[2],
            bindings=json.loads(row[3]),
            conflicts=json.loads(row[4]),
            metadata=json.loads(row[5]),
        )

    def close(self) -> None:
        """Close database connection."""
        # SQLite connections are per-call, but cleanup if needed
        pass
