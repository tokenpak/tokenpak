import requests
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

VAULT_ENTRIES_DIR = Path(os.path.expanduser("~/vault/.tokenpak/entries"))


class QueryBriefing:
    """Query TokenPak analytics and format for daily briefing.
    
    Tries HTTP query API first; falls back to direct JSONL reads if the
    query routes aren't mounted on the server yet.
    """
    
    def __init__(self, query_url: str = "http://localhost:8766"):
        self.query_url = query_url
    
    # ------------------------------------------------------------------
    # Internal: direct JSONL fallback
    # ------------------------------------------------------------------

    def _read_jsonl(self, date: str) -> list:
        """Read JSONL entries for a date directly from vault storage."""
        path = VAULT_ENTRIES_DIR / f"{date}.jsonl"
        if not path.exists():
            return []
        entries = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return entries

    def _local_usage_summary(self, date: str) -> Dict[str, Any]:
        """Compute usage summary directly from JSONL (no HTTP)."""
        entries = self._read_jsonl(date)
        if not entries:
            return {
                "date": date,
                "total_requests": 0,
                "total_tokens": 0,
                "cache_tokens": 0,
                "avg_compression": 0.0,
                "unique_agents": 0,
            }
        total_tokens = sum(e.get("tokens", 0) for e in entries)
        cache_tokens = sum((e.get("extra") or {}).get("cache_tokens", 0) for e in entries)
        compression_vals = [(e.get("extra") or {}).get("compression_ratio") for e in entries]
        valid = [v for v in compression_vals if v is not None]
        avg_compression = sum(valid) / len(valid) if valid else 0.0
        unique_agents = len({e.get("agent") for e in entries if e.get("agent")})
        return {
            "date": date,
            "total_requests": len(entries),
            "total_tokens": total_tokens,
            "cache_tokens": cache_tokens,
            "avg_compression": round(avg_compression, 4),
            "unique_agents": unique_agents,
        }

    def _local_top_agents(self, date: str, limit: int = 5) -> list:
        """Compute top agents directly from JSONL (no HTTP)."""
        from collections import defaultdict
        entries = self._read_jsonl(date)
        agent_stats: dict = defaultdict(lambda: {"request_count": 0, "total_tokens": 0})
        for entry in entries:
            agent_id = entry.get("agent") or "unknown"
            agent_stats[agent_id]["request_count"] += 1
            agent_stats[agent_id]["total_tokens"] += entry.get("tokens", 0)
        result = [{"agent_id": a, **s} for a, s in agent_stats.items()]
        result.sort(key=lambda x: x["total_tokens"], reverse=True)
        return result[:limit]

    # ------------------------------------------------------------------
    # Public API (HTTP first, JSONL fallback)
    # ------------------------------------------------------------------

    def get_daily_summary(self, date: Optional[str] = None) -> Dict[str, Any]:
        """Get usage summary for a date (default: yesterday)."""
        if not date:
            date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        try:
            resp = requests.get(
                f"{self.query_url}/query/usage-summary?date={date}", timeout=5
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        
        # Fallback: read JSONL directly
        summary = self._local_usage_summary(date)
        return {"status": "ok", "summary": summary, "source": "local"}

    def get_top_agents(self, date: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
        """Get top agents by token consumption."""
        if not date:
            date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        try:
            resp = requests.get(
                f"{self.query_url}/query/top-users?date={date}&limit={limit}", timeout=5
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        
        # Fallback: read JSONL directly
        users = self._local_top_agents(date, limit)
        return {"status": "ok", "users": users, "source": "local"}

    def format_briefing(self, date: Optional[str] = None) -> str:
        """Format daily briefing as human-readable text."""
        if not date:
            date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

        summary_resp = self.get_daily_summary(date)
        top_agents_resp = self.get_top_agents(date)

        source = summary_resp.get("source", "api")

        lines = [
            "📊 **Daily Token Briefing**",
            f"Date: {date}",
            f"Source: {source}",
            "",
        ]

        # Support both "summary" and "data" keys (API vs local)
        data = summary_resp.get("summary") or summary_resp.get("data") or {}
        if data:
            lines.append("**Usage Summary:**")
            lines.append(f"  Total tokens:    {data.get('total_tokens', 0):,}")
            lines.append(f"  Total requests:  {data.get('total_requests', 0)}")
            lines.append(f"  Cache tokens:    {data.get('cache_tokens', 0):,}")
            lines.append(f"  Unique agents:   {data.get('unique_agents', 0)}")
            lines.append(f"  Avg compression: {data.get('avg_compression', 0.0):.2f}x")
            lines.append("")

        users = top_agents_resp.get("users") or top_agents_resp.get("data") or []
        if users:
            total_tokens = data.get("total_tokens", 1) or 1
            lines.append("**Top Agents (by tokens):**")
            for i, agent in enumerate(users[:5], 1):
                agent_id = agent.get("agent_id", "unknown")
                tokens = agent.get("total_tokens", 0)
                reqs = agent.get("request_count", 0)
                pct = tokens / total_tokens * 100
                lines.append(
                    f"  {i}. {agent_id}: {tokens:,} tokens ({pct:.1f}%) — {reqs} requests"
                )

        return "\n".join(lines)


# Singleton
_briefing = QueryBriefing()


def get_daily_briefing(date: Optional[str] = None) -> str:
    """Get formatted daily briefing."""
    return _briefing.format_briefing(date)
