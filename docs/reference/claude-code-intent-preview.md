# Claude Code Companion — Intent Preview (PI-2)

> Phase PI-2 ships a **read-only preview surface** for Claude Code Companion users to inspect the Intent Layer's classification + advisory output. **Nothing is applied.** No prompt mutation. No companion injection. No routing change. The surface is a reading tool, not an actor.

## How Claude Code traffic is detected

Claude Code traffic flows through TokenPak as **Anthropic Messages** requests. TokenPak detects it via three signals:

1. **Path** — `/v1/messages`, the canonical Anthropic Messages endpoint.
2. **Headers** — `anthropic-version`, `x-api-key` (or the OAuth equivalent), and the `User-Agent` containing `claude-cli`.
3. **Credential provider** — the `tokenpak-claude-code` slug (handled by `ClaudeCodeCredentialProvider` in `credential_injector.py`) injects the OAuth token + the full Claude Code client profile.

The detection is **wire-format agnostic**: the same signals apply whether the request comes from the `claude` CLI, a Claude Code companion process, or any other tool that authenticates as Claude Code.

## Why it still uses AnthropicAdapter

PI-2 deliberately keeps **two identities separate**:

| Identity | What it is | Where it lives |
|---|---|---|
| **`source_client = "claude_code"`** | The client tool that produced the request | The Claude Code CLI / companion process |
| **`format_adapter = "AnthropicAdapter"`** | The wire format (Anthropic Messages) | `tokenpak/proxy/adapters/anthropic_adapter.py` |
| **`credential_provider = "tokenpak-claude-code"`** | The auth path (OAuth + `claude-code-20250219` beta) | `tokenpak/services/routing_service/credential_injector.py` |

There is **no `ClaudeCodeAdapter`**. Claude Code is the *client identity*; Anthropic Messages is the *wire format*. They are independent dimensions: a future client tool that emits Anthropic Messages would still resolve to `AnthropicAdapter` but would carry a different `source_client` label. The PI-2 directive § 3 forbids creating a fake `ClaudeCodeAdapter`; the test suite enforces this structurally.

## How to inspect preview guidance

Two CLI surfaces:

```bash
# Full chain: classified event → policy decision → advisory suggestion → preview patch
tokenpak claude-code intent
tokenpak claude-code intent --last        # alias for default behavior
tokenpak claude-code intent --json

# Patch-only view
tokenpak claude-code patches
tokenpak claude-code patches --json
```

Both surfaces print the directive's six required labels at the top of every render:

```
· Claude Code Companion Intent Preview
· PREVIEW ONLY
· NOT APPLIED
· NO PROMPT MUTATION
· NO CLAUDE CODE INJECTION YET
· Telemetry-only; no TIP intent headers emitted
```

The labels are pinned in `tokenpak/proxy/intent_claude_code_preview.py::PREVIEW_LABELS` and tested for presence in every render path (`tests/test_intent_claude_code_preview_phase_pi_2.py::TestPreviewLabelsPresent`).

## Why patches are preview-only

The PI-1 builder (`tokenpak/proxy/intent_prompt_patch.py`) writes every `PromptPatch` row with `applied = False`. PI-1 has no production write path; PI-2 reads and labels what PI-1 wrote (or what tests / future-phase callers wrote). **No code path in PI-1 or PI-2 flips `applied` to `True`.**

The companion-side injection that *would* flip the bit lands in **PI-3** (per the PI-0 spec § 10 sub-phase 3), gated behind:

- `intent_policy.prompt_intervention.enabled = true` (default `false`)
- `intent_policy.prompt_intervention.mode = inject_guidance` (default `preview_only`)
- `intent_policy.prompt_intervention.target = companion_context` (default; user_message is reserved indefinitely)
- The Phase 2.5 confirmation handshake (each application requires explicit user gesture)

Until PI-3 lands and the operator explicitly opts in, every `PromptPatch` shown by `tokenpak claude-code patches` is observational only.

## Why no prompt mutation happens yet

Three layers of safety lock prompts byte-stable in PI-2:

1. **PI-1 builder is pure.** No I/O during evaluation; no caller-supplied substring reaches the patch fields; the patch never gets written to a request body.
2. **PI-2 read model is read-only.** Structural test: the module imports no dispatch primitives (`forward_headers`, `pool.request`, `pool.stream`).
3. **No call site exists in `server.py`.** The PI-1 builder is library code; PI-2 surfaces it for inspection. Until a future PI-3 PR explicitly wires the builder to the companion's prompt-assembly stage, no request flow invokes it.

The Anthropic adapter declares `tip.byte-preserved-passthrough` precisely because Claude Code's billing routing depends on byte fidelity (cache_control alignment, OAuth quota mode). The PI-1 eligibility rule § 7(g) hard-blocks any patch on byte-preserved routes when `target != companion_context`. Even when `target = companion_context` is allowed, PI-2 still doesn't apply anything — that's PI-3's scope.

## How this prepares PI-3 opt-in injection

PI-3 will:

- Wire the PI-1 builder into the companion's prompt-assembly stage (companion-side, **pre-bytes** — preserves the Anthropic byte-fidelity invariant downstream).
- Honor `intent_policy.prompt_intervention.enabled` + `mode = inject_guidance` config flags.
- Funnel each application through the Phase 2.5 confirmation handshake.
- Update the PI-2 surface labels (e.g. `NOT APPLIED` becomes `Applied for this one request` only on the specific row that was applied).

PI-2's read model is the **inspection contract** PI-3 will write to. The same fields, the same labels, the same identity-separation rule. PI-3 just flips `applied` on individual rows — every other invariant carries through.

## What you see on a fresh install

`tokenpak claude-code intent` on a host with no traffic yet:

```
TOKENPAK  |  Claude Code Companion Intent Preview (PI-2)
──────────────────────────────

  · Claude Code Companion Intent Preview
  · PREVIEW ONLY
  · NOT APPLIED
  · NO PROMPT MUTATION
  · NO CLAUDE CODE INJECTION YET
  · Telemetry-only; no TIP intent headers emitted

  No intent_events rows yet.

  PI-2 surfaces a Claude-Code-shaped view over the existing
  intent_events / intent_policy_decisions /
  intent_suggestions / intent_patches tables. Send a Claude
  Code request through the proxy and re-run this command.
  See `tokenpak doctor --intent` for activation.
```

After at least one Claude Code request flows through the proxy, the same command shows the full chain (event + decision + suggestion + patch) plus the identity labels.

## MCP integration (deferred)

The PI-2 directive § 4 leaves MCP integration optional. PI-2 ships **CLI-only**; an MCP-side resource (e.g. `tokenpak.claude_code.intent.latest` / `tokenpak.claude_code.patches.latest`) is **deferred to a PI-2 follow-up sub-phase**.

Rationale: the existing companion MCP server pattern (`tokenpak/companion/mcp_server/`) exposes pre-send / journal / capsule resources today. Adding a read-only intent-preview resource is straightforward but expands surface area — it crosses the companion subsystem boundary, which has its own test + import-contract gates. PI-2 keeps scope tight by pinning the data contract in the read model module + CLI; the MCP wrapper is a thin follow-up that reads the same data.

When a follow-up PR adds MCP integration, it MUST:

- Be read-only (no resource that writes to any of the four Intent Layer tables).
- Inject no prompt context automatically.
- Not mutate any request.
- Honor the same `PREVIEW_LABELS` set rendered by the CLI.

## Privacy

Same contract as Phase 0 / 2.4 / PI-1: no raw prompts, no secrets, no full credentials. The preview payload contains only:

- IDs (request_id, contract_id, decision_id, suggestion_id, patch_id) — opaque ULIDs.
- Hashes (raw_prompt_hash sha256, original_hash sha256) — never the prompt body.
- Templated text (suggestion title / message / recommended_action, patch title / message / patch_text).
- Numeric fields (confidence, intent class, mode, action).
- Boolean fields (tip_headers_emitted, applied, requires_confirmation).

The sentinel-substring privacy contract is asserted across the read model, the CLI human render, and the CLI JSON render in `tests/test_intent_claude_code_preview_phase_pi_2.py::TestPrivacyContract`.

## Files

| Path | Purpose |
|---|---|
| `tokenpak/proxy/intent_claude_code_preview.py` | read-only join over the four Intent Layer tables |
| `tokenpak/cli/_impl.py::cmd_claude_code_intent` | `tokenpak claude-code intent` entry point |
| `tokenpak/cli/_impl.py::cmd_claude_code_patches` | `tokenpak claude-code patches` entry point |
| `tokenpak/proxy/intent_events` (table) | Phase 0 source data |
| `tokenpak/proxy/intent_policy_decisions` (table) | Phase 2.1 source data |
| `tokenpak/proxy/intent_suggestions` (table) | Phase 2.4.1 source data |
| `tokenpak/proxy/intent_patches` (table) | PI-1 source data |
| `docs/reference/claude-code-intent-preview.md` | this document |

## Cross-references

- `docs/internal/specs/intent-prompt-intervention-spec-2026-04-26.md` — the PI-0 unified spec (§6 covers the Claude Code strategy; §10 covers the rollout)
- `docs/reference/claude-code-intent-injection.md` — PI-3 companion-side opt-in injection (the next sub-phase that can flip `applied = true`)
- `docs/reference/intent-layer-phase-0.md` — Phase 0 fundamentals
- `docs/reference/intent-policy-preview.md` — Phase 2.1 / 2.2 policy preview
- `docs/reference/intent-suggest-mode.md` — Phase 2.4.3 suggest mode
- `docs/internal/milestones/intent-advisory-mvp-2026-04-26.md` — Intent Advisory MVP closeout (predecessor)
- `docs/internal/milestones/claude-code-intent-guidance-mvp-2026-04-26.md` — Claude Code Intent Guidance Injection MVP closeout (this sub-arc's closeout)

PI-4 (target=`system` + `rewrite_prompt`) requires explicit Kevin approval before any code lands.
