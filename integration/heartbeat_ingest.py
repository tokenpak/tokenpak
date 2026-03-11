import requests
import json
from datetime import datetime, timezone
from typing import Optional

class HeartbeatIngest:
    """Posts heartbeat metrics to TokenPak ingest endpoint."""
    
    def __init__(self, ingest_url: str = "http://localhost:8766"):
        self.ingest_url = ingest_url
        self.endpoint = f"{ingest_url}/ingest"
    
    def log_session(self, 
                    model: str,
                    tokens: int,
                    cost: float,
                    agent: Optional[str] = None,
                    extra: Optional[dict] = None):
        """Log a session to TokenPak ingest."""
        entry = {
            "model": model,
            "tokens": tokens,
            "cost": cost,
            "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "agent": agent or "unknown",
            "extra": extra or {}
        }
        
        try:
            resp = requests.post(self.endpoint, json=entry, timeout=2)
            return resp.status_code == 200
        except Exception as e:
            return False
