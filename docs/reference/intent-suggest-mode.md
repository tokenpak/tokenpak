# Intent Layer — Suggest Mode (Phase 2.4.3)

> Phase 2.4.3 lets a host **opt in** to suggest mode. The opt-in only changes labeling — every render path now badges decisions with "Suggest mode active" when the config flips on. **Nothing about request handling changes.** No routing, no model swap, no body mutation, no header injection. Suggestions are advisory only.

## How to enable suggest mode

Create or edit `~/.tokenpak/policy.yaml` (or `$TOKENPAK_HOME/policy.yaml`):

```yaml
intent_policy:
  mode: suggest
  # All other fields use safe defaults; you only need this one
  # line to opt in.
```

Then verify the active config:

```bash
tokenpak intent config --show
tokenpak intent config --validate
tokenpak intent config --json
```

The CLI prints the active config + a "Suggest mode active" line when the opt-in is honored. The `--validate` flag re-parses the file and reports any safety overrides applied.

## Default vs. suggest

| Behavior | `mode: observe_only` (default) | `mode: suggest` (opt-in) |
|---|---|---|
| Engine generates `PolicyDecision` per request | yes | yes |
| Engine generates `PolicySuggestion` rows when eligible | yes | yes |
| Suggestion data appears in CLI / API / dashboard | yes (Phase 2.4.2) | yes |
| Surfaces badge "Suggest mode active" | no | **yes** |
| Wire-side `X-TokenPak-Suggestion-*` headers attached | **no (locked)** | **no (locked)** |
| Routing / model / provider switching | **no** | **no** |
| Body mutation | **no** | **no** |

The only observable difference between the two modes is the **labeling** on the operator-facing surfaces. The engine, the writer, the gates — all unchanged.

## Why `dry_run` remains true

The Phase 2.4 spec §10 sub-phase plan explicitly keeps `dry_run = true` through every Phase 2.4.x sub-sub-phase. Phase 2.5 will reintroduce a synchronous pause point (confirmation mode); Phase 2.6 will introduce limited budget-cap enforcement. Until those land, every request — regardless of `mode` — flows exactly as it did in Phase 2.2.

The loader **forces** `dry_run = True` regardless of what the config file says. A user who tries to flip it sees a warning logged but the runtime stays safe. This is an intentional safety invariant.

## Why `allow_auto_routing` remains false

Same reasoning. Auto-routing is gated for Phase 2.6+ ratification. The loader forces `allow_auto_routing = False` and warns on attempted overrides. Even when the engine emits `suggest_route` decisions and downstream code sees a `recommended_provider`, the dispatcher does NOT read those fields in 2.4.x.

## Why `response_headers` are disabled

The `suggestion_surface.response_headers` flag stays locked at `False`. Two reasons:

1. **Adapter capability gate.** Wire-side `X-TokenPak-Suggestion-*` emission would still need to flow through Standard #23 §4.3 — the adapter has to declare `tip.intent.contract-headers-v1`. No first-party adapter does today (Phase 0 default; verified in `tests/test_intent_layer_phase0.py`).
2. **Byte-fidelity rule.** Architecture §5.1 keeps non-TIP providers byte-stable. Sending unsolicited TIP headers risks observable side-effects — billing routing on Anthropic OAuth, signature mismatches on SigV4 — that the Phase 2.4 spec explicitly rules out.

The loader forces `response_headers = False` and warns on attempted overrides.

## Per-surface visibility flags

```yaml
intent_policy:
  mode: suggest
  show_suggestions: true     # auto-set when mode = suggest
  suggestion_surface:
    cli: true                # default true; set false to hide CLI badging
    dashboard: true          # default true; set false to hide dashboard badging
    api: true                # default true; set false to hide API badging
    response_headers: false  # locked False in 2.4.3
```

Each flag controls **only** whether that surface adds the "Suggest mode active" badge. The underlying suggestion data still shows on every surface that already showed it in Phase 2.4.2 (with the standard "advisory / no-op / default-off" labeling). The flags add an additional active-mode badge, not a kill-switch on the data.

## What comes later in confirm mode

Phase 2.5 will introduce `mode: confirm`. With confirm mode:

- The engine still emits `PolicyDecision` + `PolicySuggestion` (same as 2.4.x).
- Decisions carrying `requires_confirmation = true` will pause the request synchronously.
- A new SDK / CLI surface presents the decision to the user; the request resumes only after the user accepts (default) or rejects (skips the suggested action).
- `dry_run` is still locked `True` until Phase 2.6.

Phase 2.6 will introduce `mode: enforce` for budget caps only:

- `flag_budget_risk` decisions will be honored — request blocked, downsized, or downgraded based on budget config.
- Auto-routing remains locked off.
- Provider switching remains locked off.

The current spec for both is `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md`. Each gets its own ratification before any code lands.

## Operator commands

```bash
# Inspect the active config
tokenpak intent config --show
tokenpak intent config --json

# Re-parse the file and surface warnings (safety overrides applied,
# invalid values, reserved-mode rejections)
tokenpak intent config --validate

# Same config snapshot also surfaces in:
tokenpak doctor --intent
tokenpak doctor --explain-last
```

## Privacy

Same contract as Phase 2.4.1 / 2.4.2: no raw prompt content, no secrets, no full credentials. The config snapshot rendered on each surface contains only the boolean / numeric / mode-name fields documented above. The loader is read-only; it never writes to disk.

## Files

| Path | Purpose |
|---|---|
| `tokenpak/proxy/intent_policy_engine.py` | `PolicyEngineConfig` dataclass extended with `show_suggestions` + `suggestion_surface` |
| `tokenpak/proxy/intent_policy_config_loader.py` | YAML loader, safety overrides, warnings list |
| `tokenpak/cli/_impl.py::cmd_intent_config` | `tokenpak intent config` entry point |
| `tokenpak/proxy/intent_doctor.py` | doctor `--intent` / `--explain-last` config snapshot |
| `tokenpak/proxy/intent_policy_dashboard.py` | dashboard payload `metadata.active_policy_config` + `suggest_mode_active` |
| `~/.tokenpak/policy.yaml` (operator-edited) | the config file |
| `docs/reference/intent-suggest-mode.md` | this document |

## Cross-references

- Phase 2 spec (parent): `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md`
- Phase 2.4 sub-spec: `docs/internal/specs/phase2.4-suggest-mode-spec-2026-04-26.md` (§3 covers the schema; §10 covers the rollout plan)
- Standard #23 §4.3 — wire-emission capability gate
- Architecture §5.1 — byte-fidelity rule
- Architecture §7.1 — prompt-locality rule
