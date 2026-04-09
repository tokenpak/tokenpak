# TokenPak Cost Budget Configuration

## Configuration Schema

Add to `tokenpak.json` under root level:

```json
{
  "cost_budget": {
    "enabled": true,
    "daily_limit": 100.0,
    "weekly_limit": 500.0
  }
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | bool | No | Enable/disable budget tracking (default: true) |
| `daily_limit` | float | No | Daily spend limit in USD |
| `weekly_limit` | float | No | Weekly spend limit in USD |

### Examples

#### Example 1: Daily Budget Only
```json
{
  "cost_budget": {
    "daily_limit": 50.0
  }
}
```

#### Example 2: Both Daily and Weekly
```json
{
  "cost_budget": {
    "daily_limit": 75.0,
    "weekly_limit": 350.0
  }
}
```

#### Example 3: Disabled
```json
{
  "cost_budget": {
    "enabled": false
  }
}
```

## Alert Thresholds

The budget tracker automatically fires alerts at the following thresholds:

- **80%**: WARNING — approaching limit
- **100%**: CRITICAL — at or exceeding limit
- **110%**: OVERAGE — 10%+ over limit

### Alert Cooldown

Alerts have a 5-minute cooldown to prevent notification spam. The same alert level won't fire twice within 5 minutes.

## Usage

### Display Budget Status
```bash
tokenpak cost show-budget
```

Output:
```
📊 TokenPak Budget Status
========================================
Daily limit: $100.00
Weekly limit: $500.00
Alert cooldown: 5 minutes

Recent Alerts:
  • daily_WARNING: 2026-03-24T22:45:00+00:00
========================================
```

### Programmatic Usage

```python
from tokenpak.cost.budget_tracker import BudgetTracker

# Load config
config = {
    "daily_limit": 100.0,
    "weekly_limit": 500.0
}

tracker = BudgetTracker(config)

# Check if spending exceeds limit
is_over, limit = tracker.check_spending_vs_limit(110.0, "daily")
# is_over: True, limit: 100.0

# Check if alert should fire
alert = tracker.should_alert(80.0, 100.0, "daily")
if alert:
    print(f"Alert: {alert}")  # WARNING alert

# Get display
display = tracker.format_budget_display(75.0, 100.0, "daily")
print(display)  # [███████░░░] 75% of daily budget ($75.00 / $100.00)
```

## Integration with Proxy

The proxy should integrate budget checks on the request path:

```python
from tokenpak.cost.budget_tracker import BudgetTracker
from tokenpak.telemetry.cost import get_current_spend

# Initialize tracker from config
budget_config = config.get("cost_budget", {})
tracker = BudgetTracker(budget_config)

# On each request:
async def handle_request(request):
    current_spend = get_current_spend()  # From telemetry

    # Check daily budget
    alert = tracker.should_alert(
        current_spend["daily"],
        tracker.config.daily_limit,
        "daily"
    )
    if alert:
        log_budget_alert(alert)

    # Continue request processing...
```

## Dashboard Integration

The dashboard should display budget progress:

```python
# In dashboard component
budget_display = tracker.format_budget_display(
    current_spend=75.0,
    limit=100.0,
    limit_type="daily"
)
# Display: [███████░░░] 75% of daily budget ($75.00 / $100.00)
```

## Testing

See `tokenpak/cost/test_budget_tracker.py` for 36+ test cases covering:
- Budget config loading
- Spending vs limit checks
- Alert thresholds (80%, 100%, 110%)
- Alert cooldown (no duplicates within window)
- Edge cases (zero, None, boundary)
- Budget display formatting
- Alert history tracking

Run tests:
```bash
pytest tokenpak/cost/test_budget_tracker.py -v
```
