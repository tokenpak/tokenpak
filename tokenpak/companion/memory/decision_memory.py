"""
Decision Memory Module

Stores and retrieves decisions made by agents, indexed by query hash.
Supports confidence learning: tracks success/failure outcomes and adjusts confidence accordingly.

Schema:
- id: unique record ID
- query_hash: SHA256 hash of the query (for deduplication)
- decision: the decision/action recommended
- confidence: initial confidence score (0.0-1.0)
- timestamp: when the decision was recorded
- outcome: observed outcome (null until recorded)

Database: SQLite at ~/.tokenpak/memory.db
Table: decisions

Example usage:
    db = DecisionMemoryDB()
    db.record("Should we use BM25?", "Yes, for <10K blocks", confidence=0.8)
    results = db.retrieve(query_hash, top_k=5)  # sorted by confidence
    db.update_confidence(record_id, new_confidence=0.85)
"""

import sqlite3
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DecisionRecord:
    """A single decision with metadata."""
    id: str
    query_hash: str
    query: Optional[str]  # original query (for reference)
    decision: str
    confidence: float  # 0.0-1.0
    timestamp: str  # ISO format
    outcome: Optional[str] = None
    success: Optional[bool] = None
    notes: Optional[str] = None


class DecisionMemoryDB:
    """
    SQLite-backed decision memory store.
    
    Stores decisions indexed by query hash for fast retrieval and learning.
    Confidence scores are updated based on observed outcomes.
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the database.
        
        Args:
            db_path: path to SQLite database (default: ~/.tokenpak/memory.db)
        """
        if db_path is None:
            db_path = os.path.expanduser("~/.tokenpak/memory.db")
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._init_schema()
    
    def _init_schema(self):
        """Initialize the database schema if it doesn't exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # decisions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    query_hash TEXT NOT NULL,
                    query TEXT,
                    decision TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    outcome TEXT,
                    success INTEGER,
                    notes TEXT
                )
            """)
            
            # Index on query_hash for fast retrieval
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_hash 
                ON decisions(query_hash)
            """)
            
            # Index on confidence for sorting
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_confidence 
                ON decisions(confidence DESC)
            """)
            
            conn.commit()
    
    def record(
        self,
        query: str,
        decision: str,
        confidence: float = 0.7,
        notes: Optional[str] = None
    ) -> str:
        """
        Record a new decision.
        
        Args:
            query: the query/question
            decision: the decision made
            confidence: initial confidence (0.0-1.0, default 0.7)
            notes: optional notes
        
        Returns:
            record ID (UUID-style)
        """
        # Validate confidence
        confidence = max(0.0, min(1.0, float(confidence)))
        
        # Hash the query for deduplication
        query_hash = hashlib.sha256(query.lower().encode()).hexdigest()
        
        # Generate record ID
        record_id = f"dec_{hashlib.md5(f'{query_hash}_{datetime.now(timezone.utc).isoformat()}'.encode()).hexdigest()[:8]}"
        
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO decisions
                (id, query_hash, query, decision, confidence, timestamp, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (record_id, query_hash, query, decision, confidence, timestamp, notes))
            conn.commit()
        
        return record_id
    
    def retrieve(
        self,
        query: Optional[str] = None,
        query_hash: Optional[str] = None,
        top_k: int = 5
    ) -> List[DecisionRecord]:
        """
        Retrieve decisions by query or query_hash, sorted by confidence (descending).
        
        Args:
            query: the query string (will be hashed)
            query_hash: pre-computed query hash (alternative to query)
            top_k: max results to return (default 5)
        
        Returns:
            List of DecisionRecords sorted by confidence (highest first)
        """
        if query is not None:
            query_hash = hashlib.sha256(query.lower().encode()).hexdigest()
        
        if query_hash is None:
            raise ValueError("Either query or query_hash must be provided")
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM decisions
                WHERE query_hash = ?
                ORDER BY confidence DESC
                LIMIT ?
            """, (query_hash, top_k))
            
            rows = cursor.fetchall()
        
        records = []
        for row in rows:
            records.append(DecisionRecord(
                id=row['id'],
                query_hash=row['query_hash'],
                query=row['query'],
                decision=row['decision'],
                confidence=row['confidence'],
                timestamp=row['timestamp'],
                outcome=row['outcome'],
                success=bool(row['success']) if row['success'] is not None else None,
                notes=row['notes']
            ))
        
        return records
    
    def update_confidence(self, record_id: str, new_confidence: float) -> bool:
        """
        Update the confidence score for a decision.
        
        Args:
            record_id: the record ID to update
            new_confidence: new confidence value (0.0-1.0)
        
        Returns:
            True if updated, False if record not found
        """
        # Validate confidence
        new_confidence = max(0.0, min(1.0, float(new_confidence)))
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE decisions
                SET confidence = ?
                WHERE id = ?
            """, (new_confidence, record_id))
            
            conn.commit()
            return cursor.rowcount > 0
    
    def record_outcome(
        self,
        record_id: str,
        outcome: str,
        success: bool,
        notes: Optional[str] = None
    ) -> bool:
        """
        Record the outcome of a decision and optionally adjust confidence.
        
        Args:
            record_id: the record ID
            outcome: description of what happened
            success: whether the outcome was successful
            notes: optional notes
        
        Returns:
            True if updated, False if record not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get current record to adjust confidence
            cursor.execute("""
                SELECT confidence FROM decisions WHERE id = ?
            """, (record_id,))
            
            row = cursor.fetchone()
            if row is None:
                return False
            
            current_confidence = row[0]
            
            # Adjust confidence based on success
            if success:
                new_confidence = min(1.0, current_confidence + 0.05)
            else:
                new_confidence = max(0.0, current_confidence - 0.1)
            
            # Record outcome
            cursor.execute("""
                UPDATE decisions
                SET outcome = ?, success = ?, confidence = ?, notes = ?
                WHERE id = ?
            """, (outcome, 1 if success else 0, new_confidence, notes, record_id))
            
            conn.commit()
            return cursor.rowcount > 0
    
    def get(self, record_id: str) -> Optional[DecisionRecord]:
        """
        Retrieve a specific decision by ID.
        
        Args:
            record_id: the record ID
        
        Returns:
            DecisionRecord or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM decisions WHERE id = ?
            """, (record_id,))
            
            row = cursor.fetchone()
        
        if row is None:
            return None
        
        return DecisionRecord(
            id=row['id'],
            query_hash=row['query_hash'],
            query=row['query'],
            decision=row['decision'],
            confidence=row['confidence'],
            timestamp=row['timestamp'],
            outcome=row['outcome'],
            success=bool(row['success']) if row['success'] is not None else None,
            notes=row['notes']
        )
    
    def all(self, order_by: str = "timestamp DESC") -> List[DecisionRecord]:
        """
        Retrieve all decisions.
        
        Args:
            order_by: SQL ORDER BY clause (default: newest first)
        
        Returns:
            List of all DecisionRecords
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute(f"""
                SELECT * FROM decisions
                ORDER BY {order_by}
            """)
            
            rows = cursor.fetchall()
        
        records = []
        for row in rows:
            records.append(DecisionRecord(
                id=row['id'],
                query_hash=row['query_hash'],
                query=row['query'],
                decision=row['decision'],
                confidence=row['confidence'],
                timestamp=row['timestamp'],
                outcome=row['outcome'],
                success=bool(row['success']) if row['success'] is not None else None,
                notes=row['notes']
            ))
        
        return records
    
    def count(self) -> int:
        """Return total number of decisions."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM decisions")
            return cursor.fetchone()[0]
    
    def delete(self, record_id: str) -> bool:
        """
        Delete a decision by ID.
        
        Args:
            record_id: the record ID to delete
        
        Returns:
            True if deleted, False if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM decisions WHERE id = ?", (record_id,))
            conn.commit()
            return cursor.rowcount > 0
    
    def clear(self) -> None:
        """Clear all decisions from the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM decisions")
            conn.commit()
