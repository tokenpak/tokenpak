# TokenPak Recipes

A collection of practical, tested how-to guides for real-world TokenPak use cases. Each recipe is a complete example you can copy-paste and adapt to your needs.

## Recipe Index

| # | Recipe | Use Case | Difficulty | Time |
|---|--------|----------|-----------|------|
| 1 | [Multi-Provider Fallback](./01-multi-provider-fallback.md) | Route to Anthropic if OpenAI is down | Beginner | 10 min |
| 2 | [Budget Caps & Spend Alerts](./02-budget-caps.md) | Set daily/monthly spend limits with alerts | Intermediate | 15 min |
| 3 | [Per-User Rate Limiting](./03-per-user-rate-limiting.md) | Assign different rate limits to different users | Intermediate | 12 min |
| 4 | [Model Routing by Use Case](./04-model-routing-by-use-case.md) | Route coding to GPT-4, chat to Haiku automatically | Intermediate | 15 min |
| 5 | [Cost Monitoring & Observability](./05-cost-monitoring.md) | Export metrics to Prometheus/Grafana dashboards | Advanced | 20 min |
| 6 | [Streaming Responses](./06-streaming-responses.md) | Receive responses token-by-token in real-time | Advanced | 15 min |
| 7 | [Local Development with Mock](./07-local-development-mock.md) | Test with mock provider, zero API costs in dev | Beginner | 10 min |

---

## Quick Start by Use Case

### "I want to save money"
1. Start with **[Local Development with Mock](./07-local-development-mock.md)** — dev environment costs nothing
2. Add **[Budget Caps](./02-budget-caps.md)** — production guardrails
3. Implement **[Model Routing by Use Case](./04-model-routing-by-use-case.md)** — choose the right model for each task

### "I want reliability"
1. Start with **[Multi-Provider Fallback](./01-multi-provider-fallback.md)** — handle provider outages
2. Add **[Per-User Rate Limiting](./03-per-user-rate-limiting.md)** — prevent users from overwhelming you
3. Implement **[Cost Monitoring](./05-cost-monitoring.md)** — see problems before they surprise you

### "I want the best user experience"
1. Start with **[Streaming Responses](./06-streaming-responses.md)** — feel 10x faster
2. Add **[Model Routing by Use Case](./04-model-routing-by-use-case.md)** — best model for each request
3. Implement **[Cost Monitoring](./05-cost-monitoring.md)** — ensure reliability

---

## Recipe Format

Every recipe follows this structure:

1. **What this solves** — one-sentence summary
2. **Prerequisites** — what you need to have set up
3. **Config Snippet** — copy-paste-ready YAML configuration
4. **Test & Verify** — step-by-step commands to validate it works
5. **What Just Happened** — explanation of how it works
6. **Common Pitfalls** — mistakes to avoid (with ✅ correct way)

## Testing Recipes

All recipes are **runnable and tested**. To verify a recipe:

1. Copy the **Config Snippet** to `config.yaml`
2. Run the **Test & Verify** commands
3. You should see the **Expected output** shown in the recipe

If you get different output, check the **Common Pitfalls** section.

## Combining Recipes

Recipes can be combined! For example:

```yaml
# config.yaml: "Production Hardened" setup
# - Multi-provider fallback (reliability)
# - Budget caps (cost control)
# - Per-user rate limiting (fairness)
# - Cost monitoring (visibility)
# - Streaming (UX)

providers:
  openai: { type: openai, api_key: ${OPENAI_API_KEY} }
  anthropic: { type: anthropic, api_key: ${ANTHROPIC_API_KEY} }

models:
  gpt-4:
    provider: openai
    fallback_to: claude-3-sonnet  # From recipe 1

budget:  # From recipe 2
  enabled: true
  daily_limit_cents: 1000
  alert_threshold_pct: 80

rate_limit:  # From recipe 3
  enabled: true
  default_rps: 10
  user_tiers:
    free: { rps: 5 }
    pro: { rps: 50 }

metrics:  # From recipe 5
  enabled: true
  prometheus: { enabled: true, port: 8001 }

streaming:  # From recipe 6
  enabled: true
  formats: [sse, chunked]
```

## Troubleshooting

### "tokenpak validate-config fails"
- Check syntax: YAML is whitespace-sensitive
- Verify all keys exist: `providers`, `models`, etc.
- Run: `tokenpak validate-config config.yaml --verbose` for details

### "Config validates but recipe doesn't work"
- Check environment variables: `echo $OPENAI_API_KEY`
- Check provider keys are valid: `tokenpak test-provider openai`
- Check TokenPak version: `tokenpak --version` (need v1.0+)

### "Recipe works locally but not in production"
- Config differences: check `config.prod.yaml` vs dev
- Environment variables: prod machine has different `.env`?
- Resource limits: token rate limits too low? Check `tokenpak status`

---

## Next Steps

After completing a recipe:

1. **Modify it** — adapt config to your use case (different budgets, models, users)
2. **Automate it** — integrate into CI/CD or systemd service
3. **Monitor it** — add alerting, metrics, dashboards (see recipe 5)
4. **Document it** — save your final config, add comments for team

---

## Questions?

- **Config help:** `tokenpak validate-config --help`
- **List all providers:** `tokenpak list-providers`
- **List all models:** `tokenpak list-models`
- **Health check:** `tokenpak status`
- **Logs:** `tokenpak logs [--provider openai] [--tail 50]`
