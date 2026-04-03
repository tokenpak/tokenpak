"""tokenpak/agent/query/audit.py — Cost Audit Summary & Breakdown

Lightweight cost breakdown by model and feature for the "where did my money go?"
question. No agent attribution (which agent triggered what), just aggregated costs.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class AuditGenerator:
    """Generate cost breakdowns by model and feature."""

    def __init__(self):
        pass

    def model_breakdown(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Calculate total cost by model.

        Returns:
            {
                "models": {
                    "claude-sonnet-4-6": {"cost": 12.34, "requests": 45, "percentage": 45.2},
                    "claude-opus-4-5": {"cost": 15.01, "requests": 23, "percentage": 54.8},
                },
                "total_cost": 27.35,
                "total_requests": 68,
            }
        """
        model_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"cost": 0.0, "requests": 0})
        total_cost = 0.0
        total_requests = 0

        for entry in entries:
            model = entry.get("model", "unknown")
            cost = entry.get("cost", 0.0)

            model_stats[model]["cost"] += cost
            model_stats[model]["requests"] += 1
            total_cost += cost
            total_requests += 1

        # Add percentages
        result = {}
        for model, stats in model_stats.items():
            pct = (stats["cost"] / total_cost * 100) if total_cost > 0 else 0.0
            result[model] = {
                "cost": round(stats["cost"], 6),
                "requests": stats["requests"],
                "percentage": round(pct, 2),
            }

        # Sort by cost descending
        sorted_models = sorted(result.items(), key=lambda x: x[1]["cost"], reverse=True)

        return {
            "models": {k: v for k, v in sorted_models},
            "total_cost": round(total_cost, 6),
            "total_requests": total_requests,
        }

    def feature_breakdown(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Calculate cost attribution by feature.

        Features:
        - base: Standard model inference
        - caching: Token caching (cache hits)
        - compression: Token compression
        - tools: Tool use

        Returns:
            {
                "features": {
                    "base": {"cost": 18.5, "tokens": 50000, "percentage": 67.6},
                    "caching": {"cost": 5.2, "tokens": 20000, "percentage": 19.0},
                    "compression": {"cost": 3.1, "tokens": 15000, "percentage": 11.3},
                    "tools": {"cost": 0.55, "tokens": 500, "percentage": 2.0},
                },
                "total_cost": 27.35,
            }
        """
        feature_stats: dict[str, dict[str, float]] = {
            "base": {"cost": 0.0, "tokens": 0},
            "caching": {"cost": 0.0, "tokens": 0},
            "compression": {"cost": 0.0, "tokens": 0},
            "tools": {"cost": 0.0, "tokens": 0},
        }
        total_cost = 0.0

        for entry in entries:
            cost = entry.get("cost", 0.0)
            total_tokens = entry.get("tokens", entry.get("total_tokens", 0))
            extra = entry.get("extra") or {}

            base_cost = cost

            # Cache tokens: tokens * (cost_per_token * 0.1) due to 90% discount
            cache_tokens = extra.get("cache_tokens", 0)
            if cache_tokens > 0:
                cost_per_token = cost / max(total_tokens, 1)
                cache_cost = cache_tokens * cost_per_token * 0.1  # 90% discount on cache reads
                base_cost -= cache_cost * 0.9  # Subtract the savings from base
            else:
                cache_cost = 0.0

            # Compression: estimated savings
            compressed_tokens = extra.get("compressed_tokens", 0)
            if compressed_tokens > 0:
                cost_per_token = cost / max(total_tokens, 1)
                compression_cost = compressed_tokens * cost_per_token * 0.15  # ~15% savings
                base_cost -= compression_cost * 0.85
            else:
                compression_cost = 0.0

            # Tool cost: attributed based on tool_tokens
            tool_tokens = extra.get("tool_tokens", 0)
            if tool_tokens > 0:
                cost_per_token = cost / max(total_tokens, 1)
                tool_cost = tool_tokens * cost_per_token
                base_cost -= tool_cost
            else:
                tool_cost = 0.0

            # Ensure base cost doesn't go negative
            base_cost = max(base_cost, cost * 0.1)

            feature_stats["base"]["cost"] += base_cost
            feature_stats["caching"]["cost"] += cache_cost
            feature_stats["compression"]["cost"] += compression_cost
            feature_stats["tools"]["cost"] += tool_cost

            feature_stats["base"]["tokens"] += max(
                total_tokens - cache_tokens - compressed_tokens - tool_tokens, 0
            )
            feature_stats["caching"]["tokens"] += cache_tokens
            feature_stats["compression"]["tokens"] += compressed_tokens
            feature_stats["tools"]["tokens"] += tool_tokens

            total_cost += cost

        # Add percentages and round
        result = {}
        for feature, stats in feature_stats.items():
            pct = (stats["cost"] / total_cost * 100) if total_cost > 0 else 0.0
            result[feature] = {
                "cost": round(stats["cost"], 6),
                "tokens": int(stats["tokens"]),
                "percentage": round(pct, 2),
            }

        # Sort by cost descending
        sorted_features = sorted(result.items(), key=lambda x: x[1]["cost"], reverse=True)

        return {
            "features": {k: v for k, v in sorted_features},
            "total_cost": round(total_cost, 6),
        }

    def combined_breakdown(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Combined model + feature breakdown.

        Returns both breakdowns plus cross-analysis.
        """
        models = self.model_breakdown(entries)
        features = self.feature_breakdown(entries)

        return {
            "period": "custom",
            "models": models["models"],
            "features": features["features"],
            "total_cost": round(models["total_cost"], 6),
            "total_requests": models["total_requests"],
            "summary": {
                "top_model": next(iter(models["models"].keys())) if models["models"] else None,
                "top_feature": next(iter(features["features"].keys()))
                if features["features"]
                else None,
                "model_count": len(models["models"]),
                "feature_breakdown_available": True,
            },
        }

    def session_audit(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Quick audit for current session (simple format).

        Returns a simplified audit for display on dashboard.
        """
        breakdown = self.combined_breakdown(entries)

        return {
            "total_spend": breakdown["total_cost"],
            "request_count": breakdown["total_requests"],
            "avg_cost_per_request": round(
                breakdown["total_cost"] / max(breakdown["total_requests"], 1), 6
            ),
            "models": breakdown["models"],
            "features": breakdown["features"],
            "generated_at": None,  # Will be filled by caller
        }
