# Phase 4.2 — Scaffold Renderer Expansion

**Date**: 2026-04-25
**Branch**: `feat/phase4.2-scaffold-renderer-expansion`
**Predecessor**: PR #42 (Phase 4.1 — Scaffold Hardening, merged as `98d565f6`)

---

## Scope

Deterministic template expansion only. Three new / extended renderers
covering the next-tier provider integrations beyond the Phase 4.1 MVP.

Per Kevin's directive (2026-04-25): **no cloud-wrapper renderers** —
Bedrock generic, Anthropic-on-Vertex, IBM watsonx, SigV4 scaffolding,
OAuth scaffolding, and `--llm-assist` are all explicitly held.

---

## Deliverables

### 1. `openai-chat + api-key-header` — auth header parameterized

The Phase 4.1 renderer hardcoded `_AUTH_HEADER = "api-key"` (Azure
convention). Phase 4.2 adds a CLI flag so vendor-specific headers
(`X-API-Key`, `Api-Key`, etc.) emit at scaffold-time without a
post-edit.

| Field | Value |
|---|---|
| Renderer key | `openai_chat_apikey` (unchanged) |
| New CLI flag | `--auth-header NAME` |
| Default | `api-key` (Azure) for `openai-chat`; `x-api-key` for `anthropic-messages` |
| ScaffoldParams field | `auth_header: Optional[str] = None` |
| Strip-set updated | Lowercased configured header added alongside `authorization` + `x-api-key` |

### 2. `anthropic-messages + api-key-header` — new renderer

Anthropic Messages API shape, `x-api-key` auth, required
`anthropic-version: 2023-06-01` header. Capability declarations are
**explicit and empty by default** — no implicit TIP support, opt-in
per capability after the maintainer reviews the upstream's
body-rewrite tolerance.

| Field | Value |
|---|---|
| Renderer key | `anthropic_messages_apikey` (new) |
| Class structure | Standalone (no base class), `_AUTH_HEADER` + `_ANTHROPIC_VERSION` constants |
| Capabilities | `frozenset()` — explicit empty; comment lists candidate `tip.*` flags |
| Request fixture | `max_tokens` required, `system` as top-level field, no `temperature` |
| Response fixture | `content` blocks (`type: "text"`), `usage.input_tokens` / `usage.output_tokens` |
| Test class | `TestCapabilityDeclarations` asserts no implicit TIP support |
| Strip-set | Includes `anthropic-version` so caller variants don't override the proxy's value |

### 3. `openai-chat + bearer-passthrough` — new renderer

For OpenRouter-style providers that need extra/non-standard request
body fields (`provider`, `transforms`, `route`, etc.) preserved
end-to-end. Identical to Pattern A at the InjectionPlan level (no
`body_transform` set → body forwarded byte-for-byte) but adds an
explicit `_BODY_PASSTHROUGH = True` annotation + a `TestBodyPassThrough`
test class asserting the contract.

| Field | Value |
|---|---|
| Renderer key | `openai_chat_bearer_passthrough` (new) |
| New auth value | `bearer-passthrough` (added to `KNOWN_AUTHS`) |
| Class annotation | `_BODY_PASSTHROUGH = True` |
| Test class | `TestBodyPassThrough` — asserts `plan.body_transform is None` |

---

## Dogfood runs

Three runs against synthetic `--out-dir` paths in `/tmp/`:

| Slug | Family | Auth | Header | Notes |
|---|---|---|---|---|
| `tokenpak-acme-xapi` | `openai-chat` | `api-key-header` | `--auth-header X-API-Key` | Verifies #1 |
| `tokenpak-anthropic-stub` | `anthropic-messages` | `api-key-header` | (default `x-api-key`) | Verifies #2 |
| `tokenpak-openrouter-passthrough` | `openai-chat` | `bearer-passthrough` | + `--extra-header HTTP-Referer` + `X-Title` | Verifies #3 |

All three runs:
- Produce 5 files (provider class, test file, request fixture, response fixture, docs stub) + paste-ready follow-up issue.
- Generate a provider class that passes `python3 -m ruff check` standalone.
- Compile via `py_compile.compile(..., doraise=True)`.
- Generate a test file that passes ruff once placed in canonical position (`tests/test_<vendor>_offline.py` alongside `tokenpak/services/routing_service/extras/<vendor>.py`). Standalone ruff in `/tmp/` reports `I001` because the imported `extras.<vendor>` module hasn't been placed yet — this is a tooling artifact (ruff's first-party detection requires the module to exist on disk), not a generator bug. Verified by copying generated files into the canonical positions and re-running ruff: clean.
- Pass JSON-validity checks on both fixtures.

---

## Acceptance criteria

| Criterion | Status |
|---|---|
| New renderers are deterministic templates | ✅ no LLM, classifier-dispatched |
| Dry-run works for each renderer | ✅ verified for anthropic + bearer-passthrough |
| Non-interactive mode fails safely on missing fields | ✅ `TestPhase42NonInteractiveSafety` covers slug + endpoint |
| Generated code follows Standard #23 | ✅ slug regex, class naming, `live_verified=False` default, capability declaration form |
| Generated files pass ruff | ✅ provider class standalone; test file in canonical position |
| Generated offline tests pass | ✅ verified via `pytest tests/test_scaffold.py` (110 tests) |
| Extra headers represented in fixtures/tests | ✅ `_render_extra_header_test_block` reused for bearer-passthrough; explicit assertion classes elsewhere |
| `live_verified=False` remains default | ✅ all three renderers emit `live_verified = False` |
| No live credentials required | ✅ AST-level guardrail enforced; no `--llm-assist` |
| No destructive registration by default | ✅ `--register` remains opt-in (Phase 4.1) |
| Existing scaffold tests remain green | ✅ 110 / 110 pass (was 80 in Phase 4.1) |
| Full suite remains green | ✅ 940 pass / 1 skipped / 1 xfailed (was 910 baseline) |

---

## What was NOT done (per directive)

- AWS Bedrock generic
- Anthropic Claude on Vertex (`publishers/anthropic/...` URL + envelope)
- IBM watsonx
- SigV4 scaffolding
- OAuth scaffolding
- `--llm-assist` (still stubbed; exits 2)
- Any cloud-wrapper renderer family

Held for future explicit direction.

---

## Test count delta

| Suite | Phase 4.1 | Phase 4.2 | Δ |
|---|---|---|---|
| `tests/test_scaffold.py` | 80 | 110 | +30 |
| Full suite (excluding baseline 404s) | 910 | 940 | +30 |

New test classes:

- `TestApiKeyHeaderConfigurable` — 5 tests covering default + custom auth header emission, lowercased strip-set entry, ruff cleanliness.
- `TestBearerPassthroughRenderer` — 8 tests covering classifier dispatch, `_BODY_PASSTHROUGH` annotation, `_EnvKeyBearerProvider` inheritance, ruff/compile, `TestBodyPassThrough` class generation, extra-header support.
- `TestAnthropicMessagesApikeyRenderer` — 14 tests covering classifier dispatch, default + custom auth header, `anthropic-version` emission, explicit empty capabilities, ruff/compile, fixture shape (Anthropic-specific keys), test-file capability class, docs stub, dry-run.
- `TestPhase42NonInteractiveSafety` — 3 tests covering missing endpoint + invalid slug for the new renderers.
