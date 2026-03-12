#!/usr/bin/env python3
"""
TokenPak Phase 3 — Stage 1 Baseline Metrics Collection
Collects: throughput, latency, error rates, cache hit rates, session distribution
Period: 2026-03-11 to 2026-03-18 (7 days)
Deployment: All 3 machines (Sue/Trix/Cali) with proxy_v4.py, all toggles OFF
"""

import os
import json
import sqlite3
import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any

class BaselineMetricsCollector:
    """Collect and export baseline metrics from proxy_v4.py telemetry."""
    
    def __init__(self, db_path: str = "monitor.db", csv_output: str = "baseline-week1-2026-03-11-to-2026-03-18.csv"):
        self.db_path = db_path
        self.csv_output = csv_output
        self.start_date = datetime(2026, 3, 11)
        self.end_date = datetime(2026, 3, 18)
        
    def verify_baseline_state(self) -> Dict[str, Any]:
        """Verify proxy_v4.py is running with all toggles OFF."""
        toggles = {
            "TOKENPAK_SEMANTIC_CACHE": os.environ.get("TOKENPAK_SEMANTIC_CACHE", "0"),
            "TOKENPAK_TRACE": os.environ.get("TOKENPAK_TRACE", "0"),
            "TOKENPAK_REQUEST_LOGGER": os.environ.get("TOKENPAK_REQUEST_LOGGER", "0"),
        }
        
        all_off = all(v in ("0", "false", "no", "off", "") for v in toggles.values())
        return {
            "proxy_running": Path("monitor.db").exists(),
            "toggles": toggles,
            "all_toggles_off": all_off,
            "timestamp": datetime.now().isoformat()
        }
    
    def collect_request_throughput(self) -> Dict[str, Any]:
        """Collect request throughput (requests/min, avg latency)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Query request counts and latencies
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_requests,
                    AVG(response_time) as avg_latency_ms,
                    MIN(response_time) as min_latency_ms,
                    MAX(response_time) as max_latency_ms
                FROM requests
                WHERE timestamp BETWEEN ? AND ?
            """, (self.start_date.isoformat(), self.end_date.isoformat()))
            
            result = cursor.fetchone()
            conn.close()
            
            return {
                "total_requests": result[0] if result[0] else 0,
                "avg_latency_ms": round(result[1], 2) if result[1] else None,
                "min_latency_ms": round(result[2], 2) if result[2] else None,
                "max_latency_ms": round(result[3], 2) if result[3] else None,
                "requests_per_minute": round((result[0] or 0) / (7 * 24 * 60), 2)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def collect_error_rates(self) -> Dict[str, Any]:
        """Collect error rates by provider."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Query error rates by provider
            cursor.execute("""
                SELECT 
                    provider,
                    COUNT(*) as total,
                    SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) as errors,
                    ROUND(100.0 * SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) / COUNT(*), 2) as error_rate_pct
                FROM requests
                WHERE timestamp BETWEEN ? AND ?
                GROUP BY provider
            """, (self.start_date.isoformat(), self.end_date.isoformat()))
            
            results = cursor.fetchall()
            conn.close()
            
            return {
                row[0]: {
                    "total": row[1],
                    "errors": row[2],
                    "error_rate_pct": row[3]
                }
                for row in results
            }
        except Exception as e:
            return {"error": str(e)}
    
    def collect_cache_metrics(self) -> Dict[str, Any]:
        """Collect cache hit rates (semantic cache still OFF but tracked)."""
        return {
            "semantic_cache_enabled": False,
            "status": "Cache metrics collection skipped — TOKENPAK_SEMANTIC_CACHE=0",
            "note": "Cache will be enabled in Stage 2 (2026-03-19)"
        }
    
    def collect_session_distribution(self) -> Dict[str, Any]:
        """Collect session count and duration distribution."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    COUNT(DISTINCT session_id) as unique_sessions,
                    AVG(session_duration_seconds) as avg_duration_s,
                    MIN(session_duration_seconds) as min_duration_s,
                    MAX(session_duration_seconds) as max_duration_s
                FROM sessions
                WHERE start_time BETWEEN ? AND ?
            """, (self.start_date.isoformat(), self.end_date.isoformat()))
            
            result = cursor.fetchone()
            conn.close()
            
            return {
                "unique_sessions": result[0] if result[0] else 0,
                "avg_duration_seconds": round(result[1], 2) if result[1] else None,
                "min_duration_seconds": round(result[2], 2) if result[2] else None,
                "max_duration_seconds": round(result[3], 2) if result[3] else None,
            }
        except Exception as e:
            return {"error": str(e)}
    
    def export_csv(self, metrics: Dict[str, Any]) -> str:
        """Export baseline metrics to CSV."""
        with open(self.csv_output, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Metric", "Value"])
            
            for key, value in metrics.items():
                if isinstance(value, dict):
                    for subkey, subvalue in value.items():
                        writer.writerow([f"{key}.{subkey}", subvalue])
                else:
                    writer.writerow([key, value])
        
        return self.csv_output
    
    def collect_all(self) -> Dict[str, Any]:
        """Collect all baseline metrics."""
        return {
            "collection_period": f"{self.start_date.date()} to {self.end_date.date()}",
            "baseline_state": self.verify_baseline_state(),
            "request_throughput": self.collect_request_throughput(),
            "error_rates_by_provider": self.collect_error_rates(),
            "cache_metrics": self.collect_cache_metrics(),
            "session_distribution": self.collect_session_distribution(),
            "timestamp": datetime.now().isoformat()
        }

if __name__ == "__main__":
    collector = BaselineMetricsCollector()
    metrics = collector.collect_all()
    
    print(json.dumps(metrics, indent=2))
    csv_file = collector.export_csv(metrics)
    print(f"\nMetrics exported to: {csv_file}")
