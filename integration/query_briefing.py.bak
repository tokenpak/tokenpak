import requests
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

class QueryBriefing:
    """Query TokenPak analytics and format for daily briefing."""
    
    def __init__(self, query_url: str = "http://localhost:8765"):
        self.query_url = query_url
    
    def get_daily_summary(self, date: Optional[str] = None) -> Dict[str, Any]:
        """Get usage summary for a date (default: yesterday)."""
        if not date:
            date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        try:
            resp = requests.get(f"{self.query_url}/query/usage-summary?date={date}", timeout=5)
            return resp.json() if resp.status_code == 200 else {"error": resp.status_code}
        except Exception as e:
            return {"error": str(e)}
    
    def get_top_agents(self, date: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
        """Get top agents by token consumption."""
        if not date:
            date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        try:
            resp = requests.get(f"{self.query_url}/query/top-users?date={date}&limit={limit}", timeout=5)
            return resp.json() if resp.status_code == 200 else {"error": resp.status_code}
        except Exception as e:
            return {"error": str(e)}
    
    def format_briefing(self, date: Optional[str] = None) -> str:
        """Format daily briefing as human-readable text."""
        summary = self.get_daily_summary(date)
        top_agents = self.get_top_agents(date)
        
        if "error" in summary:
            return f"⚠️ Query error: {summary.get('error')}"
        
        lines = [
            "📊 **Daily Token Briefing**",
            f"Date: {date or 'yesterday'}",
            ""
        ]
        
        # Summary metrics
        if "summary" in summary:
            data = summary["summary"]
            lines.append("**Usage Summary:**")
            lines.append(f"  Total tokens: {data.get('total_tokens', 0):,}")
            lines.append(f"  Total requests: {data.get('total_requests', 0)}")
            lines.append(f"  Cache tokens: {data.get('cache_tokens', 0):,}")
            lines.append(f"  Unique agents: {data.get('unique_agents', 0)}")
            lines.append(f"  Avg compression: {data.get('avg_compression', 1.0):.2f}x")
            lines.append("")
        
        # Top agents
        if "users" in top_agents:
            lines.append("**Top Agents (by tokens):**")
            for i, agent in enumerate(top_agents["users"][:5], 1):
                agent_id = agent.get("agent_id", "unknown")
                tokens = agent.get("total_tokens", 0)
                requests = agent.get("request_count", 0)
                pct = (tokens / summary["summary"].get('total_tokens', 1) * 100) if "summary" in summary else 0
                lines.append(f"  {i}. {agent_id}: {tokens:,} tokens ({pct:.1f}%) — {requests} requests")
        
        return "\n".join(lines)

# Singleton
_briefing = QueryBriefing()

def get_daily_briefing(date: Optional[str] = None) -> str:
    """Get formatted daily briefing."""
    return _briefing.format_briefing(date)
