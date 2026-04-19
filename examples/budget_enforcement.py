#!/usr/bin/env python3
"""
Budget Enforcement

What this example shows:
- Setting monthly cost limits
- Tracking spend against budget
- Preventing overspend with alerts
- Budget reporting and forecasting

When to use this:
- Enterprise environments with cost controls
- SaaS products with user quotas
- Development teams with monthly budgets
"""

import os
from datetime import datetime, timezone
import json


def main():
    """Demonstrate budget enforcement and tracking."""
    
    print("=" * 60)
    print("BUDGET ENFORCEMENT")
    print("=" * 60)
    print()
    
    print("TokenPak can enforce monthly budgets to prevent surprises.")
    print()
    
    print("=" * 60)
    print("Example 1: Simple Budget Check")
    print("=" * 60)
    print()
    
    print("Code:")
    print("""
class BudgetTracker:
    def __init__(self, monthly_limit: float):
        self.monthly_limit = monthly_limit
        self.spent_this_month = 0.0
        self.requests = []
    
    def check_budget(self, estimated_cost: float) -> bool:
        '''Check if request would exceed budget.'''
        if self.spent_this_month + estimated_cost > self.monthly_limit:
            return False
        return True
    
    def log_usage(self, cost: float, tokens: int, model: str):
        '''Record usage after a successful request.'''
        self.spent_this_month += cost
        self.requests.append({
            "cost": cost,
            "tokens": tokens,
            "model": model,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    
    def get_remaining_budget(self) -> float:
        '''Get remaining budget for this month.'''
        return self.monthly_limit - self.spent_this_month
    
    def get_projected_monthly_cost(self) -> float:
        '''Estimate monthly cost based on usage so far.'''
        if not self.requests:
            return 0.0
        
        days_elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(self.requests[0]["timestamp"])).days + 1
        if days_elapsed < 1:
            days_elapsed = 1
        
        avg_daily_cost = self.spent_this_month / days_elapsed
        return avg_daily_cost * 30


# Usage
budget = BudgetTracker(monthly_limit=100.0)

# Check before making request
estimated_cost = 0.50
if budget.check_budget(estimated_cost):
    # Make request...
    actual_cost = 0.48
    budget.log_usage(actual_cost, tokens=1500, model="claude-sonnet-4-6")
    print(f"Request succeeded. Remaining: ${budget.get_remaining_budget():.2f}")
else:
    print(f"Request blocked. Budget exceeded.")
    print(f"Remaining: ${budget.get_remaining_budget():.2f}")
    """)
    
    print()
    print("=" * 60)
    print("Example 2: Real Budget Tracking")
    print("=" * 60)
    print()
    
    # Simulate a month of usage
    monthly_budget = 100.0
    
    requests_this_month = [
        {"date": "2026-03-01", "cost": 5.32, "tokens": 15000, "model": "sonnet"},
        {"date": "2026-03-02", "cost": 4.18, "tokens": 12000, "model": "sonnet"},
        {"date": "2026-03-03", "cost": 6.45, "tokens": 18500, "model": "sonnet"},
        {"date": "2026-03-04", "cost": 3.92, "tokens": 11000, "model": "haiku"},
        {"date": "2026-03-05", "cost": 7.25, "tokens": 20000, "model": "sonnet"},
        {"date": "2026-03-06", "cost": 5.88, "tokens": 16500, "model": "sonnet"},
        {"date": "2026-03-07", "cost": 8.41, "tokens": 24000, "model": "sonnet"},
        {"date": "2026-03-08", "cost": 4.33, "tokens": 12000, "model": "haiku"},
        {"date": "2026-03-09", "cost": 6.19, "tokens": 17500, "model": "sonnet"},
        {"date": "2026-03-10", "cost": 5.67, "tokens": 16000, "model": "sonnet"},
    ]
    
    total_spent = sum(r["cost"] for r in requests_this_month)
    total_tokens = sum(r["tokens"] for r in requests_this_month)
    remaining = monthly_budget - total_spent
    
    print(f"Monthly Budget: ${monthly_budget:.2f}")
    print()
    
    print("Usage by date:")
    print("-" * 60)
    for req in requests_this_month:
        pct = (total_spent / monthly_budget) * 100
        print(f"  {req['date']}: ${req['cost']:>6.2f}  ({req['tokens']:>6,} tokens, {req['model']})")
    
    print()
    print(f"Total spent:     ${total_spent:>8.2f} ({100*total_spent/monthly_budget:.1f}% of budget)")
    print(f"Total tokens:    {total_tokens:>8,}")
    print(f"Average/day:     ${total_spent/len(requests_this_month):>8.2f}")
    print(f"Remaining:       ${remaining:>8.2f}")
    print()
    
    if remaining < monthly_budget * 0.1:
        print("⚠️  WARNING: Budget usage is high (>90%)")
    elif remaining < monthly_budget * 0.2:
        print("⚠️  CAUTION: Budget usage is moderate (>80%)")
    else:
        print("✅ Budget status: Healthy")
    
    print()
    
    # Projection
    projected_daily = total_spent / len(requests_this_month)
    days_left = 30 - len(requests_this_month)
    projected_total = total_spent + (projected_daily * days_left)
    
    print("Month Projection:")
    print("-" * 60)
    print(f"Days elapsed:     {len(requests_this_month)}")
    print(f"Days remaining:   {days_left}")
    print(f"Avg daily spend:  ${projected_daily:.2f}")
    print(f"Projected total:  ${projected_total:.2f}")
    
    if projected_total > monthly_budget:
        overage = projected_total - monthly_budget
        print(f"⚠️  Projected overage: ${overage:.2f}")
        print(f"    Action: Reduce usage by {100*overage/monthly_budget:.1f}% to stay under budget")
    else:
        buffer = monthly_budget - projected_total
        print(f"✅ Projected buffer: ${buffer:.2f}")
    
    print()
    print("=" * 60)
    print("Budget Alert Strategies")
    print("=" * 60)
    print()
    print("1. Soft limit (80% of budget):")
    print("   • Alert when approaching limit")
    print("   • Suggest optimization without blocking")
    print()
    print("2. Hard limit (100% of budget):")
    print("   • Block requests when limit exceeded")
    print("   • Allow emergency override with approval")
    print()
    print("3. Per-user quotas:")
    print("   • Allocate budget to teams/users")
    print("   • Track individual spend")
    print()
    print("4. Model-specific budgets:")
    print("   • Different limits for different models")
    print("   • Encourage cheaper models when possible")
    print()
    
    print("=" * 60)
    print("Cost Optimization Tips")
    print("=" * 60)
    print()
    print("1. Use cache to reduce input costs by 90%")
    print("2. Switch to cheaper models when possible (Haiku vs Sonnet)")
    print("3. Batch requests for better cache hits")
    print("4. Set aggressive budgets to force optimization")
    print("5. Monitor and alert on daily burn rate changes")
    print()
    
    return 0


if __name__ == "__main__":
    exit(main())
