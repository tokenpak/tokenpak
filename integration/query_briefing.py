import requests
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

VAULT_ENTRIES_DIR = Path(os.path.expanduser("~/vault/.tokenpak/entries"))


def _read_jsonl_for_date(date: str) -> list:
    """Read entries from JSONL file for a given date."""
    path = VAULT_ENTRIES_DIR / f"{date}.jsonl"
    entries = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return entries


def _summarize_entries(entries: list) -> Dict[str, Any]:
    """Compute summary metrics from a list of entries."""
    total_tokens = sum(e.get("tokens", 0) for e in entries)
    total_cost = sum(e.get("cost", 0.0) for e in entries)
    agents = {e.get("agent", "unknown") for e in entries}
    models = {e.get("model", "unknown") for e in entries}
    return {
        "total_tokens": total_tokens,
        "total_requests": len(entries),
        "total_cost": total_cost,
        "unique_agents": len(agents),
        "unique_models": len(models),
    }


def _top_agents(entries: list, limit: int = 5) -> list:
    """Return top agents sorted by token consumption."""
    agent_stats = defaultdict(lambda: {"total_tokens": 0, "total_cost": 0.0, "request_count": 0})
    for e in entries:
        agent = e.get("agent", "unknown")
        agent_stats[agent]["total_tokens"] += e.get("tokens", 0)
        agent_stats[agent]["total_cost"] += e.get("cost", 0.0)
        agent_stats[agent]["request_count"] += 1
    return sorted(
        [{"agent_id": k, **v} for k, v in agent_stats.items()],
        key=lambda x: x["total_tokens"],
        reverse=True
    )[:limit]


class QueryBriefing:
    """Query TokenPak analytics and format for daily briefing.
    
    Tries HTTP query endpoints first; falls back to direct JSONL reads
    when the query router is not mounted on the running server.
    """
    
    def __init__(self, query_url: str = "http://localhost:8765"):
        self.query_url = query_url
    
    def get_daily_summary(self, date: Optional[str] = None) -> Dict[str, Any]:
        """Get usage summary for a date (default: yesterday)."""
        if not date:
            date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        # Try HTTP first
        try:
            resp = requests.get(f"{self.query_url}/query/usage-summary?date={date}", timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        
        # Fallback: read JSONL directly
        entries = _read_jsonl_for_date(date)
        return {"summary": _summarize_entries(entries), "source": "local", "date": date}
    
    def get_top_agents(self, date: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
        """Get top agents by token consumption."""
        if not date:
            date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        # Try HTTP first
        try:
            resp = requests.get(f"{self.query_url}/query/top-users?date={date}&limit={limit}", timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        
        # Fallback: read JSONL directly
        entries = _read_jsonl_for_date(date)
        return {"users": _top_agents(entries, limit), "source": "local", "date": date}
    
    def format_briefing(self, date: Optional[str] = None) -> str:
        """Format daily briefing as human-readable text."""
        if not date:
            date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

        summary = self.get_daily_summary(date)
        top_agents = self.get_top_agents(date)
        source = summary.get("source", "http")
        
        lines = [
            "📊 **Daily Token Briefing**",
            f"Date: {date} (source: {source})",
            ""
        ]
        
        # Summary metrics
        data = summary.get("summary") or summary.get("data") or {}
        if data:
            total = data.get("total_tokens", 0)
            lines.append("**Usage Summary:**")
            lines.append(f"  Total tokens: {total:,}")
            lines.append(f"  Total requests: {data.get('total_requests', 0)}")
            lines.append(f"  Total cost: ${data.get('total_cost', 0.0):.4f}")
            lines.append(f"  Unique agents: {data.get('unique_agents', 0)}")
            lines.append(f"  Unique models: {data.get('unique_models', 0)}")
            lines.append("")
        else:
            lines.append("  (no data for this date)")
            lines.append("")
        
        # Top agents
        users = top_agents.get("users") or top_agents.get("data") or []
        if users:
            total_tokens = data.get("total_tokens", 1) or 1
            lines.append("**Top Agents (by tokens):**")
            for i, agent in enumerate(users[:5], 1):
                agent_id = agent.get("agent_id", "unknown")
                tokens = agent.get("total_tokens", 0)
                reqs = agent.get("request_count", 0)
                pct = tokens / total_tokens * 100
                lines.append(f"  {i}. {agent_id}: {tokens:,} tokens ({pct:.1f}%) — {reqs} requests")
        
        return "\n".join(lines)


# Singleton
_briefing = QueryBriefing()

def get_daily_briefing(date: Optional[str] = None) -> str:
    """Get formatted daily briefing."""
    return _briefing.format_briefing(date)
