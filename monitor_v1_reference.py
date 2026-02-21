#!/usr/bin/env python3
"""
TokenPak Monitor — Live tracking of token savings

Logs all LLM requests and tracks compression metrics in real-time.
"""

import json
import time
import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

# Pricing per 1K tokens (input) - update as needed
MODEL_PRICING = {
    # OpenAI
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    
    # Anthropic
    "claude-opus-4": {"input": 0.015, "output": 0.075},
    "claude-sonnet-4": {"input": 0.003, "output": 0.015},
    "claude-sonnet-4-5": {"input": 0.003, "output": 0.015},
    "claude-haiku-3-5": {"input": 0.0008, "output": 0.004},
    
    # Google
    "gemini-pro": {"input": 0.00025, "output": 0.0005},
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
    "gemini-1.5-flash": {"input": 0.000075, "output": 0.0003},
    
    # Default fallback
    "default": {"input": 0.01, "output": 0.03},
}


@dataclass
class RequestLog:
    """Single request log entry."""
    id: Optional[int] = None
    timestamp: str = ""
    model: str = ""
    request_type: str = ""  # chat, completion, embedding, etc.
    
    # Token counts
    original_input_tokens: int = 0
    compressed_input_tokens: int = 0
    output_tokens: int = 0
    
    # Costs
    original_cost: float = 0.0
    compressed_cost: float = 0.0
    savings: float = 0.0
    savings_percent: float = 0.0
    
    # Metadata
    context_files: str = ""  # JSON list of files included
    compression_ratio: float = 0.0
    
    def to_dict(self) -> dict:
        return asdict(self)


class TokenPakMonitor:
    """Monitors and logs TokenPak compression metrics."""
    
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = Path.home() / ".openclaw" / "workspace" / ".ocp" / "monitor.db"
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._init_db()
        self._lock = threading.Lock()
    
    def _init_db(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                request_type TEXT,
                original_input_tokens INTEGER,
                compressed_input_tokens INTEGER,
                output_tokens INTEGER,
                original_cost REAL,
                compressed_cost REAL,
                savings REAL,
                savings_percent REAL,
                context_files TEXT,
                compression_ratio REAL
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON requests(timestamp)
        """)
        
        conn.commit()
        conn.close()
    
    def get_pricing(self, model: str) -> dict:
        """Get pricing for a model."""
        # Normalize model name
        model_lower = model.lower()
        
        for key in MODEL_PRICING:
            if key in model_lower:
                return MODEL_PRICING[key]
        
        return MODEL_PRICING["default"]
    
    def log_request(
        self,
        model: str,
        original_input_tokens: int,
        compressed_input_tokens: int,
        output_tokens: int = 0,
        request_type: str = "chat",
        context_files: List[str] = None
    ) -> RequestLog:
        """Log a request with compression metrics."""
        
        pricing = self.get_pricing(model)
        
        # Calculate costs
        original_cost = (original_input_tokens / 1000) * pricing["input"]
        compressed_cost = (compressed_input_tokens / 1000) * pricing["input"]
        output_cost = (output_tokens / 1000) * pricing["output"]
        
        original_cost += output_cost
        compressed_cost += output_cost
        
        savings = original_cost - compressed_cost
        savings_percent = (savings / original_cost * 100) if original_cost > 0 else 0
        compression_ratio = original_input_tokens / max(compressed_input_tokens, 1)
        
        log = RequestLog(
            timestamp=datetime.now().isoformat(),
            model=model,
            request_type=request_type,
            original_input_tokens=original_input_tokens,
            compressed_input_tokens=compressed_input_tokens,
            output_tokens=output_tokens,
            original_cost=round(original_cost, 6),
            compressed_cost=round(compressed_cost, 6),
            savings=round(savings, 6),
            savings_percent=round(savings_percent, 2),
            context_files=json.dumps(context_files or []),
            compression_ratio=round(compression_ratio, 2)
        )
        
        # Save to database
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO requests (
                    timestamp, model, request_type,
                    original_input_tokens, compressed_input_tokens, output_tokens,
                    original_cost, compressed_cost, savings, savings_percent,
                    context_files, compression_ratio
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                log.timestamp, log.model, log.request_type,
                log.original_input_tokens, log.compressed_input_tokens, log.output_tokens,
                log.original_cost, log.compressed_cost, log.savings, log.savings_percent,
                log.context_files, log.compression_ratio
            ))
            
            log.id = cursor.lastrowid
            conn.commit()
            conn.close()
        
        return log
    
    def get_recent(self, limit: int = 50) -> List[RequestLog]:
        """Get recent requests."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM requests
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [RequestLog(**dict(row)) for row in rows]
    
    def get_stats(self, hours: int = 24) -> dict:
        """Get aggregated stats for time period."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        since = datetime.now().isoformat()[:10]  # Start of today
        
        cursor.execute("""
            SELECT
                COUNT(*) as total_requests,
                SUM(original_input_tokens) as total_original_tokens,
                SUM(compressed_input_tokens) as total_compressed_tokens,
                SUM(output_tokens) as total_output_tokens,
                SUM(original_cost) as total_original_cost,
                SUM(compressed_cost) as total_compressed_cost,
                SUM(savings) as total_savings,
                AVG(compression_ratio) as avg_compression_ratio,
                AVG(savings_percent) as avg_savings_percent
            FROM requests
            WHERE timestamp >= ?
        """, (since,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row[0] == 0:
            return {
                "total_requests": 0,
                "total_original_tokens": 0,
                "total_compressed_tokens": 0,
                "total_output_tokens": 0,
                "total_original_cost": 0,
                "total_compressed_cost": 0,
                "total_savings": 0,
                "avg_compression_ratio": 0,
                "avg_savings_percent": 0,
            }
        
        return {
            "total_requests": row[0] or 0,
            "total_original_tokens": row[1] or 0,
            "total_compressed_tokens": row[2] or 0,
            "total_output_tokens": row[3] or 0,
            "total_original_cost": round(row[4] or 0, 4),
            "total_compressed_cost": round(row[5] or 0, 4),
            "total_savings": round(row[6] or 0, 4),
            "avg_compression_ratio": round(row[7] or 0, 2),
            "avg_savings_percent": round(row[8] or 0, 2),
        }
    
    def get_by_model(self) -> Dict[str, dict]:
        """Get stats grouped by model."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT
                model,
                COUNT(*) as requests,
                SUM(original_input_tokens) as original_tokens,
                SUM(compressed_input_tokens) as compressed_tokens,
                SUM(savings) as total_savings,
                AVG(compression_ratio) as avg_ratio
            FROM requests
            GROUP BY model
            ORDER BY total_savings DESC
        """)
        
        rows = cursor.fetchall()
        conn.close()
        
        return {
            row[0]: {
                "requests": row[1],
                "original_tokens": row[2],
                "compressed_tokens": row[3],
                "total_savings": round(row[4], 4),
                "avg_ratio": round(row[5], 2)
            }
            for row in rows
        }


# Simulated data generator for testing
def generate_test_data(monitor: TokenPakMonitor, count: int = 20):
    """Generate test data to populate the dashboard."""
    import random
    
    models = [
        "gpt-4o",
        "gpt-4o-mini", 
        "claude-sonnet-4-5",
        "claude-haiku-3-5",
        "gemini-1.5-flash"
    ]
    
    request_types = ["chat", "completion", "agent", "tool_call"]
    
    files_options = [
        ["SOUL.md", "MEMORY.md"],
        ["SOUL.md", "AGENTS.md", "HEARTBEAT.md"],
        ["MEMORY.md"],
        ["SOUL.md", "MEMORY.md", "AGENTS.md", "TOOLS.md"],
        ["policy.md", "faq.md"],
    ]
    
    for i in range(count):
        model = random.choice(models)
        
        # Simulate realistic token counts
        original = random.randint(1500, 8000)
        
        # Compression varies by content type
        ratio = random.uniform(1.1, 3.5)
        compressed = int(original / ratio)
        
        output = random.randint(200, 1500)
        
        log = monitor.log_request(
            model=model,
            original_input_tokens=original,
            compressed_input_tokens=compressed,
            output_tokens=output,
            request_type=random.choice(request_types),
            context_files=random.choice(files_options)
        )
        
        print(f"  [{i+1}/{count}] {model}: {original} → {compressed} tokens (${log.savings:.4f} saved)")
        
        time.sleep(0.1)  # Small delay for realistic timestamps


if __name__ == "__main__":
    print("TokenPak Monitor — Test Data Generator")
    print("=" * 50)
    
    monitor = TokenPakMonitor()
    
    print("\nGenerating 20 test requests...\n")
    generate_test_data(monitor, 20)
    
    print("\n" + "=" * 50)
    print("Today's Stats:")
    stats = monitor.get_stats()
    print(f"  Requests: {stats['total_requests']}")
    print(f"  Original tokens: {stats['total_original_tokens']:,}")
    print(f"  Compressed tokens: {stats['total_compressed_tokens']:,}")
    print(f"  Total savings: ${stats['total_savings']:.4f}")
    print(f"  Avg compression: {stats['avg_compression_ratio']}x")
    
    print("\nBy Model:")
    by_model = monitor.get_by_model()
    for model, data in by_model.items():
        print(f"  {model}: {data['requests']} requests, ${data['total_savings']:.4f} saved")
