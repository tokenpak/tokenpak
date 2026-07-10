# Value Proof

`tokenpak prove` runs the same multi-turn prompt scenario through two paths:
direct API and TokenPak. It then prints a side-by-side report for input tokens,
cache-read tokens, output tokens, cost, and elapsed time.

Use it when you want a workload-specific proof before changing a client setup,
or when you want to compare the same scenario before and after a compression
configuration change.

## Quick Start

```bash
# Run the built-in default scenario.
tokenpak prove run

# List available scenarios.
tokenpak prove list

# Run a named custom scenario.
tokenpak prove run my-scenario

# Override the scenario model.
tokenpak prove run default --model gpt-4o

# Show a saved proof result.
tokenpak prove show prf_a1b2c3d4

# Create a new scenario.
tokenpak prove create --name my-scenario
```

## Reading the Report

The report compares each arm against the first arm, which is the direct API
baseline by default.

| Row | Meaning |
|---|---|
| Input tokens | Prompt tokens sent at full input price. |
| Cache-read tokens | Prompt-cache tokens billed at the provider cache-read rate. |
| Output tokens | Response tokens returned by the provider. |
| Total cost | Estimated provider cost for the run. |
| Total time | End-to-end time for the run. |

Negative deltas in the comparison column mean the TokenPak arm used fewer
tokens, cost less, or finished faster than the baseline for that scenario.
Output token counts should normally match; a change there means the prompt or
model behavior should be inspected.

## Custom Scenarios

Scenarios are Markdown files with YAML frontmatter and `## Turn` headings:

```markdown
---
name: My Scenario
model: claude-sonnet-4-6
provider: anthropic
max_tokens: 4096
---

## Turn 1: Initial question

Your first prompt here.

## Turn 2: Follow-up

Your follow-up that references context from Turn 1.
```

Store custom scenarios at `~/.tokenpak/prove/scenarios/<name>.md`, then run
`tokenpak prove list` to confirm discovery.

## Saved Results

Each run saves a JSON result under `~/.tokenpak/prove/results/`. Use the proof
ID shown at the bottom of the report with `tokenpak prove show <proof_id>` to
replay the report later.

## See Also

- [CLI Reference: `tokenpak prove`](../cli-reference.md#tokenpak-prove)
- [Telemetry and Dashboard](telemetry.md)
