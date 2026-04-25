# Phase 4 Spec — `tokenpak adapter scaffold --from-docs <url>`

**Status**: design / not implemented.
**Authoritative standard**: [`23-provider-adapter-standard.md`](https://github.com/kaywhy331/obsidian-vault/blob/main/02_COMMAND_CENTER/tokenpak-standards-internal/23-provider-adapter-standard.md) (vault, internal). Generated code MUST conform to that standard.
**Companion artifact**: [`docs/internal/adapter-standardization-report-2026-04-25.md`](./adapter-standardization-report-2026-04-25.md) — the post-merge audit of the eleven-provider build that informed both #23 and this spec.

This document specifies what the codegen tool does, what it accepts, what it produces, what it refuses to do, and how to classify a provider into the right output template. It does NOT specify implementation. The implementer (whoever picks up Phase 4) reads this spec + Standard #23 and writes the tool.

---

## 1. Goals + non-goals

### 1.1 Goals

- **Reduce the marginal cost of adding a new provider** from "read the docs, copy an existing provider, edit + test + verify" (~1-3 hours per provider) to "scaffold + verify + edit if needed" (~10-30 minutes for the common case).
- **Enforce Standard #23 by construction** — the scaffold tool emits code that's pre-compliant with naming, InjectionPlan composition, capability declaration, optional-dependency policy, and test conventions. A provider added via the tool can't accidentally violate the standard's surface rules.
- **Encode tribal knowledge** — the eleven existing providers represent five recurring patterns (Standard #23 §2.4). The tool's decision tree picks the right pattern automatically given the provider's wire shape.
- **Defer human review only where genuinely needed** — the tool flags exactly which generated lines need a maintainer's eye (auth secrets handling, novel envelopes, etc.) instead of every line being suspect.

### 1.2 Non-goals

- **Not an LLM-codegen-driven tool by default.** The decision tree is rules-based, sourced from the standard + the existing patterns. An optional `--llm-assist` flag MAY use an LLM to extract structured data from a docs URL when the docs aren't machine-readable, but the codegen itself is templated + deterministic. (Standardization Report §5 explicitly framed this as decision-tree-shaped, not template-engine-shaped — meaning the *interaction* is decision-tree-shaped; the *code emission* IS template-driven once the decisions are made.)
- **Not a live-API testing tool.** Scaffolding emits offline contract tests + fixtures. Live verification is a separate manual step (or a future tool — out of scope here).
- **Not a deployment tool.** Scaffolding writes files. It does NOT register the new provider with `register()`, modify `__all__`, edit the registry, or commit anything. The maintainer reviews + commits.
- **Not a docs-site publisher.** The tool emits a docs stub the maintainer can drop into `tokenpak/docs`. Site sync is a downstream concern.

---

## 2. Command UX

### 2.1 Surface

```
tokenpak adapter scaffold --from-docs <url> [options]
```

Lives under `tokenpak adapter` namespace, mirroring the existing `tokenpak claude` / `tokenpak codex` launcher pattern. Subcommand grouping reserves `tokenpak adapter` for adapter-management tooling (likely future siblings: `tokenpak adapter list`, `tokenpak adapter doctor`).

### 2.2 Mandatory flags

| Flag | Type | Description |
|---|---|---|
| `--from-docs <url>` | URL | Provider's API documentation entry point |
| `--slug <name>` | string | Provider slug per Standard #23 §1.1 (`tokenpak-<vendor>[-<product-or-family>]`) |

### 2.3 Optional flags (override decision tree)

When the tool can't or shouldn't infer from docs, the maintainer pins choices:

| Flag | Values | Default behavior if omitted |
|---|---|---|
| `--family` | `openai-chat`, `openai-responses`, `anthropic-messages`, `gemini`, `bedrock-wrapper`, `vertex-wrapper`, `cli-bridge`, `custom` | Inferred from docs by classifier |
| `--auth` | `bearer`, `api-key-header`, `sigv4`, `oauth-adc`, `oauth-token-file`, `custom` | Inferred from docs |
| `--endpoint` | URL pattern, e.g. `https://api.x.ai/v1/chat/completions` or `https://{region}-x.googleapis.com/v1/...` | Inferred from docs |
| `--streaming` | `same-url`, `verb-suffix`, `path-suffix`, `none` | Inferred from docs |
| `--optional-dep` | comma-list of pip packages (e.g. `boto3,botocore`) | None |
| `--live-verified` | `yes`, `no` | `no` (defaults to offline-only per Standard #23 §6.3) |
| `--out-dir` | path | `tokenpak/services/routing_service/credential_injector.py` (CredentialProvider) + `tests/test_<slug>_offline.py` for the test |
| `--dry-run` | flag | Print what would be written; write nothing |
| `--non-interactive` | flag | Fail on any inference ambiguity instead of prompting |
| `--llm-assist` | flag | Use an LLM to extract structured data from the docs URL when the doc isn't pip-friendly (rate-limited, expensive — opt-in) |

### 2.4 Default mode is interactive

Without `--non-interactive`, the tool walks an inference pass over the docs URL, then asks the maintainer to confirm each ambiguous decision:

```
$ tokenpak adapter scaffold --from-docs https://docs.x-provider.com/api --slug tokenpak-x-provider

[scaffold] Fetching docs...
[scaffold] Inferred:
  family    = openai-chat (matched: /chat/completions endpoint, messages array, choices[].message)
  auth      = bearer (matched: Authorization: Bearer <key> in Authentication section)
  endpoint  = https://api.x-provider.com/v1/chat/completions
  streaming = same-url (matched: stream:true body flag, SSE response)
  optional  = none (no SDK references found)

[scaffold] Some choices need confirmation:

  Q1. Live verification?
      [y] yes  — I have an X_PROVIDER_API_KEY available, will verify after merge
      [n] no   — ship offline-only, set live_verified=False, open follow-up issue
      Default: n  →
```

`--non-interactive` short-circuits on any ambiguity; CI / scripted use must pin every flag.

### 2.5 Output

```
[scaffold] Writing:
  tokenpak/services/routing_service/credential_injector.py  (+1 class, +1 register call)
  tests/test_phase{N}_x_provider.py                          (NEW, ~150 LOC)
  tests/fixtures/x_provider/                                 (NEW dir, request.json, response.json)
  docs/integrations/x-provider.md                            (NEW stub)

[scaffold] Open follow-up issues (suggested):
  - "Verify tokenpak-x-provider live route once X_PROVIDER_API_KEY is available"

[scaffold] Next steps:
  1. Review the generated CredentialProvider — auth + URL shape
  2. Run: pytest tests/test_phase{N}_x_provider.py
  3. Run: ruff check tokenpak/ tests/
  4. Open a PR per Standard #21 (branching policy)
```

The tool exits 0 on success (files written, even if maintainer-review-required diffs), 1 on inference failure or guardrail violation.

---

## 3. Required inputs

The tool accepts inputs via flags AND extracts them from the docs URL. When it can't extract, it asks (interactive) or fails (non-interactive).

| Input | Source priority | Notes |
|---|---|---|
| **Provider docs URL** | Required flag | Used as the source-of-truth. Must be reachable. The tool fetches once + caches; later flags can override what it inferred. |
| **Provider slug** | Required flag | Validated against Standard #23 §1.1. Tool refuses to proceed if slug doesn't match `tokenpak-<vendor>[-<product-or-family>]` shape OR if it collides with an existing slug. |
| **Request format family** | `--family` flag → docs inference → ambiguous-prompt | Drives which template the tool uses. See §6 for the decision tree. |
| **Auth scheme** | `--auth` flag → docs inference → ambiguous-prompt | Determines `add_headers` shape and whether `header_resolver` is needed. Five canonical shapes covered in §6. |
| **Endpoint pattern** | `--endpoint` flag → docs inference → ambiguous-prompt | Static URL → `target_url_override`. Body-templated → `target_url_resolver`. Regional / multi-deployment patterns flagged for human review. |
| **Streaming support** | `--streaming` flag → docs inference → assumed `same-url` | If `verb-suffix` (Vertex) or `path-suffix` (Bedrock), tool flags the URL resolver as needing per-request stream awareness. |
| **Optional dependency requirements** | `--optional-dep` flag → docs inference (looks for SDK names) → assumed `none` | When set, tool emits the standard `try-import + ImportError → log + return None` block per Standard #23 §5. |
| **Live verification availability** | `--live-verified` flag → asks (default `no`) | Drives the `live_verified` class attribute and whether the docstring includes the "Live status: contract-tested offline only" line. |

### 3.1 Slug validation

The tool refuses to proceed if:

- Slug doesn't match the Standard #23 §1.1 regex.
- Slug already registered in `credential_injector.registered()` (collision check).
- Slug uses a forbidden prefix (`tokenpak-` is required; `tokenpak-X-` reserved patterns enforced).

### 3.2 Doc-fetch failure modes

- Unreachable URL → fail; suggest user provides offline copy via `--from-docs-file <path>` (alternative form).
- Doc requires auth (private API) → fail with a hint: paste relevant sections via `--from-docs-paste` (stdin alternative).
- Doc found but no inferable structure → fall through to interactive Q&A or fail under `--non-interactive`.

---

## 4. Generated artifacts

What the scaffold tool writes, in priority order. The tool generates the minimum set; some artifacts are conditional.

### 4.1 CredentialProvider class

**Always generated.** Every provider needs one.

- Inserted into `tokenpak/services/routing_service/credential_injector.py` at the documented insertion point (between the last existing provider class and the `register(...)` block).
- Subclass of `_EnvKeyBearerProvider` for the OpenAI-Chat-compatible Bearer family (Standard #23 §2.4 Pattern A) — the 5-line case.
- Custom class for Patterns C/D/E (Azure / file-OAuth / signed-request).
- Class name follows Standard #23 §1.2 (`<Slug>CredentialProvider` with CamelCase conversion).
- Class docstring includes:
  - One-sentence vendor description.
  - Required env vars (linked to docs URL).
  - Optional env vars + defaults.
  - "Live status" line per Standard #23 §6.4.

### 4.2 Format adapter (FormatAdapter subclass)

**Conditionally generated.** Only when the wire format genuinely differs from the existing 5 first-party adapters (Anthropic Messages, OpenAI Responses, OpenAI Chat, Google Generative AI, Passthrough).

- Lives in `tokenpak/proxy/adapters/<provider>_adapter.py`.
- Subclass of `FormatAdapter`; declares `source_format` + `capabilities`.
- Implements `detect`, `normalize`, `denormalize`, `get_default_upstream`, `get_sse_format`.
- Tool flags this for **mandatory human review** — novel format adapters are the highest-risk codegen output. Comment markers: `# SCAFFOLD-REVIEW: confirm wire shape`.

### 4.3 InjectionPlan wiring

**Always generated** as part of the CredentialProvider's `_load()` method body. Composed from the seven slots per Standard #23 §2.1, only including the slots the chosen pattern uses. Header set + URL pattern come from the inferred or pinned inputs.

### 4.4 Capability declarations

**Always generated.** Per Standard #23 §4, `capabilities = frozenset({...})` is mandatory. The tool emits the declared set based on the pattern:

- Pattern A/B (OpenAI-Chat-compat): inherits from existing `OpenAIChatAdapter.capabilities` (the format adapter declares; the credential provider doesn't need its own).
- Pattern E (signed-request): declares `tip.compression.v1` + `tip.cache.proxy-managed` if format adapter exists; flags for review otherwise.
- Custom format adapter: tool emits a starter set + `# SCAFFOLD-REVIEW: declare which TIP capabilities this format actually supports`.

### 4.5 Offline contract test file

**Always generated.** Written to `tests/test_<slug>_offline.py` for single-provider files OR `tests/test_phase{N}_<topic>.py` if the maintainer is grouping. Conforms to Standard #23 §6.

Test classes emitted (subset depending on pattern):

- `TestProviderResolveGate` (env-missing returns None)
- `TestAuthHeaderInjection` (correct header shape + caller-auth stripped)
- `TestUrlResolution` (when body-aware URL applies)
- `TestBodyTransform` (when applies)
- `TestHeaderResolver` (signed-request only)
- `TestProviderFieldsPassthrough` (parametrized over provider-specific fields, if any)
- `TestCostFallback` (verifies slugs miss `MODEL_COSTS` and fall back to `DEFAULT_COSTS`)
- `TestRegistration` (auto-registered, doesn't displace others)
- `TestLiveVerifiedMarker` (confirms `live_verified = False` if pinned)
- `TestAcceptanceCriteria` (PR-specific contract assertions, generated as a stub)

The OpenRouter offline test file (`tests/test_openrouter_offline.py`, PR #36) is the reference implementation.

### 4.6 Fixture files

**Always generated** for the offline tests. JSON files under `tests/fixtures/<slug>/`:

- `request.json` — representative request shape (model + messages + provider-specific fields).
- `response.json` — representative response shape (usage block, choices/output array).
- `streaming-event.json` — single SSE chunk (when streaming is in scope).

Fixtures sourced from the docs URL where the docs include examples. When docs lack examples, the tool emits a placeholder with `# SCAFFOLD-REVIEW: replace with actual fixture from <docs-url>`.

### 4.7 Docs stub

**Always generated.** Markdown file at `docs/integrations/<slug>.md` covering:

- Required env vars + how to set them.
- Supported models (linked back to the docs URL — not enumerated).
- Live verification status.
- Live-test command (curl example pointing at the local proxy).
- Troubleshooting: common 401 / 4xx surface and how to diagnose.

The stub uses the existing OpenClaw integration docs (`integrations/openclaw/README.md`) as the visual template.

### 4.8 `live_verified=False` marker by default

**Always emitted** unless `--live-verified yes` is passed:

```python
class XProviderCredentialProvider(_EnvKeyBearerProvider):
    """X Provider — ...

    **Live status: contract-tested offline only.** No
    ``X_PROVIDER_API_KEY`` was available when this route was
    scaffolded. The route compiles; the contract tests pass; live
    verification is tracked separately in issue #<NN>.
    """

    live_verified = False
    name = "tokenpak-x-provider"
    ...
```

The tool also generates issue text the maintainer can paste:

```
[scaffold] Suggested follow-up issue body (paste into `gh issue create`):

  Title: Verify tokenpak-x-provider live route once X_PROVIDER_API_KEY is available
  Body:  [text drafted per the issue #35 template]
```

The tool does NOT auto-create the issue (no GitHub API calls during scaffolding — see §5).

---

## 5. Guardrails

The tool refuses or warns on these by construction. Maintaining these guarantees is non-negotiable; the implementer should write tests for each.

### 5.1 No live API calls during scaffolding

The tool MUST NOT:

- Send any request to the provider's actual API.
- Open a connection to the provider host.
- Run a live key probe even if a key is set in env.

The only network call the tool makes is fetching the docs URL itself (one-time HTTP GET, no auth). Output of that fetch is cached locally in `~/.tokenpak/scaffold-cache/` so re-runs don't re-fetch.

### 5.2 No raw credential storage

Generated files MUST NOT contain:

- Real API key values (even as comments / examples).
- Real OAuth tokens / JWTs / SigV4 signatures.
- Anything matching the `sk-`, `eyJ`, or `AKIA` prefix patterns.

The tool runs a self-check on its own output before writing — if any string in a generated file matches a credential-shape regex, the write is aborted and the tool exits 1 with a clear error.

### 5.3 No implicit TIP support

Generated `capabilities = frozenset({...})` MUST be explicit. The tool refuses to emit:

- Empty `capabilities = frozenset()` set without an inline comment explaining why ("byte-only forwarder, no opt-in features").
- Capability labels not in the canonical TIP vocabulary (`tip.<...>`) or the `ext.` namespace.
- Capabilities the format adapter is known not to support (e.g. emitting `tip.byte-preserved-passthrough` on a pattern that re-serializes the body — flagged for human review).

### 5.4 No destructive config changes

The tool writes new files + appends to existing ones in well-defined insertion zones. It MUST NOT:

- Edit existing CredentialProvider classes (any change to existing providers is a separate manual PR).
- Reorder the `register(...)` block in arbitrary ways (insert at the end of the block, alphabetised within phase groups when grouping is consistent).
- Modify `__init__.py` `__all__` lists without an explicit + visible diff.
- Touch the `_REGISTRY` runtime list directly (registration happens via `register()` calls only).

This guardrail is the codegen analog of Standard #23 §3 (additive-only integration scripts).

### 5.5 Generated code MUST follow Standard #23

The tool's templates are derived from #23 by construction. Specifically:

- Slug + class name conform to §1.1 / §1.2.
- InjectionPlan composition follows §2's slot rules + precedence.
- Capability declarations follow §4.
- Optional-dependency handling follows §5 (lazy import inside `_load`, ImportError graceful skip, actionable log message).
- Test files + fixtures follow §6.

A `--lint` flag (or default post-write step) runs `ruff check` + `lint-imports` on the generated files; failures block the write.

---

## 6. Adapter classification decision tree

The tool's central inference: given a docs URL, which template do we use? Seven categories, each with a worked example from the existing eleven providers. Maintainer can pin via `--family` + `--auth` to override.

### 6.1 OpenAI-Chat-compatible

**Wire signal**: docs document POST `/v1/chat/completions` (or vendor-specific equivalent) with `messages` array, `choices[].message`, OpenAI Chat Completions response shape. Auth is `Authorization: Bearer <key>`.

**Template**: 5-line `_EnvKeyBearerProvider` subclass. No new format adapter (existing `OpenAIChatAdapter` handles the wire). Pattern A from Standard #23 §2.4.

**Worked examples**: `MistralCredentialProvider`, `GroqCredentialProvider`, `TogetherCredentialProvider`, `DeepSeekCredentialProvider`, `CohereCredentialProvider`, `OpenRouterCredentialProvider` (with extra-headers extension).

**Confidence**: HIGH. This is the safest scaffold output — the tool can generate it without human review for the vast majority of OpenAI-compat vendors.

### 6.2 OpenAI-Responses-compatible

**Wire signal**: docs document POST `/v1/responses` with `input` array, `output` array, `response.completed` SSE event. Auth typically Bearer (sk-) or JWT (ChatGPT OAuth).

**Template**: similar to 6.1 but inherits `OpenAIResponsesAdapter`. May need `body_transform` for Codex-style payload constraints (force `stream:true`, drop `max_output_tokens`).

**Worked examples**: `CodexCredentialProvider` (file-OAuth + body transform variant), `OpenAICodexResponsesAdapter` (the format adapter, also Pattern D's relative).

**Confidence**: MEDIUM. The tool can generate the basic shape; body_transform rules need human-confirmed extraction from the docs.

### 6.3 Anthropic-Messages-compatible

**Wire signal**: docs document POST `/v1/messages` with `messages` array, `content` blocks (text / tool_use / etc.), Anthropic SSE events (`message_start`, `content_block_delta`, etc.). Auth is `x-api-key` or OAuth with specific anthropic-beta headers.

**Template**: subclass with file-OAuth pattern OR Bearer pattern depending on auth shape. Possibly merge_headers for `anthropic-beta`. Reuses existing `AnthropicAdapter`.

**Worked examples**: `ClaudeCodeCredentialProvider` (file-OAuth + merge_headers); a hypothetical `tokenpak-anthropic-direct` (Bearer / sk-ant-*) would be Pattern A.

**Confidence**: MEDIUM-HIGH for Bearer; MEDIUM for OAuth (auth subtleties — beta header sets vary).

### 6.4 Gemini-compatible

**Wire signal**: docs document POST `/v1beta/models/<model>:generateContent` (or `:streamGenerateContent`) with `contents` array, `generationConfig` block. Auth is `x-goog-api-key`.

**Template**: subclass + body-aware URL resolver (model in URL path) + body_transform (strip `model` field, possibly `stream`). Reuses existing `GoogleGenerativeAIAdapter`.

**Worked examples**: existing direct Gemini usage isn't a credential provider yet (lives outside the credential_injector). `VertexAIGeminiCredentialProvider` is the cloud-hosted variant — see 6.6.

**Confidence**: MEDIUM. URL templating + body cleanup are mechanical but vary subtly per Gemini family.

### 6.5 Provider-native custom envelope

**Wire signal**: docs document a request/response shape that doesn't match any of 6.1-6.4. Examples: Cohere v1 (distinct from v2), IBM watsonx, Replicate, vendor-specific RPC shapes.

**Template**: NEW `FormatAdapter` subclass + matching `CredentialProvider`. The adapter handles normalize/denormalize between the vendor's shape and `CanonicalRequest`. Pattern from Standard #23 §2.4 (the bespoke case).

**Worked examples**: none yet — placeholder for IBM watsonx / Cohere v1 / Replicate scaffolding.

**Confidence**: LOW. Format adapters are the highest-risk codegen — wire shape mismatches break compression, capsule injection, and cost tracking silently. **Human review of the format adapter is mandatory.** Tool emits with `# SCAFFOLD-REVIEW:` markers on every method.

### 6.6 Cloud-hosted wrapper adapter

**Wire signal**: docs describe a cloud platform (AWS Bedrock, GCP Vertex, Azure OpenAI) hosting models from one or more vendors. The wire format is the *vendor's* shape wrapped in the *platform's* envelope (Bedrock's `InvokeModel`, Vertex's `publishers/<vendor>/...` URL prefix). Auth is platform-specific (SigV4, OAuth2 ADC, api-key with deployment routing).

**Template**: `target_url_resolver` (URL templated by region + model + sometimes deployment) + `body_transform` (strip URL-encoded fields, add envelope-required fields like `anthropic_version`) + `header_resolver` for signed auth. Pattern E from Standard #23 §2.4.

**Worked examples**: `BedrockClaudeCredentialProvider` (PR #33), `AzureOpenAICredentialProvider` (PR #32), `VertexAIGeminiCredentialProvider` (PR #34).

**Confidence**: MEDIUM. The pattern is well-documented but signed-auth implementations need careful per-platform attention. Tool flags the auth resolver for human review every time.

### 6.7 CLI/bridge adapter

**Wire signal**: provider has a local CLI binary (codex, claude) we could shell out to as an alternative to direct HTTP. Useful when:

- The CLI provides agent-loop features (sandbox, tools, AGENTS.md discovery) that the HTTP API doesn't.
- The CLI has its own auth chain that's easier to reuse than re-implementing.

**Template**: NOT a CredentialProvider. A subprocess-driving Backend (see closed PR #26's `OpenAICodexOAuthBackend`). Plus the wire-format adapter for response shaping.

**Worked examples**: PR #26 (closed; preserved on branch `feat/codex-companion-bridge`). The pattern was deemed wrong abstraction for OpenClaw's use case (Standardization Report §3) but remains relevant for genuine sub-agent scenarios.

**Confidence**: LOW. CLI bridge adapters are rare + use-case-specific. Tool emits a stub + flags the entire output for human review.

### 6.8 Decision tree summary

```
docs URL
  │
  ├─ POST /v1/chat/completions + messages array?
  │   └─ Pattern A (6.1) — high confidence, scaffolds cleanly
  │       ├─ Bearer auth?              → 5-line _EnvKeyBearerProvider
  │       └─ Bearer + extra headers?   → +EXTRA_HEADERS
  │
  ├─ POST /v1/responses + input array?
  │   └─ Pattern B (6.2) — medium confidence
  │       ├─ body needs constraints?   → +body_transform
  │       └─ JWT/file auth?            → file-OAuth pattern
  │
  ├─ POST /v1/messages + Anthropic events?
  │   └─ Pattern C (6.3) — medium-high confidence
  │       ├─ Bearer (sk-ant)?          → 5-line subclass
  │       └─ file OAuth + beta merge?  → file-OAuth + merge_headers
  │
  ├─ POST /v1beta/models/<m>:gen* + contents?
  │   └─ Pattern D (6.4) — medium confidence
  │       └─ url-resolver + body strip
  │
  ├─ Cloud-hosted (Bedrock / Vertex / Azure)?
  │   └─ Pattern E (6.6) — medium confidence, MUST review auth
  │       ├─ SigV4?                    → boto3 + header_resolver
  │       ├─ OAuth ADC?                → google-auth + header_resolver
  │       └─ api-key + deployment?     → url_resolver + api-key header
  │
  ├─ CLI bridge requested?
  │   └─ Pattern G (6.7) — LOW confidence, MUST review entire output
  │       └─ Backend + subprocess driver (NOT a CredentialProvider)
  │
  └─ None of the above → custom envelope
      └─ Pattern F (6.5) — LOWEST confidence, MUST review format adapter
          └─ NEW FormatAdapter + CredentialProvider
```

---

## 7. Test strategy

### 7.1 Tests on the scaffold tool itself

Once implemented, the tool needs:

- **Unit tests**: each classification node (6.1-6.7) in isolation. Given a synthetic docs fixture, the tool produces the expected provider class + test file shape.
- **Snapshot tests**: golden-file comparison of generated outputs against checked-in expected outputs for each pattern. Diff the snapshots in CI when the tool changes.
- **Guardrail tests**: each rule in §5 has a test that proves the tool refuses the violation case (e.g. tool tries to write a real `sk-` key → write aborts).
- **Round-trip tests**: scaffold a known-good provider against its real docs (offline cache); compare generated to existing checked-in provider; differences are either bugs in the tool or valid evolution (latter requires updating the snapshot).

### 7.2 Tests on generated providers

The scaffold tool's outputs must pass the standard test suite:

- `pytest tests/test_<provider>_offline.py` — generated test file passes.
- `ruff check tokenpak/services/routing_service/credential_injector.py tests/` — no lint failures.
- `lint-imports --config .importlinter` — no architecture violations.
- `pytest tests/test_adapter_capabilities.py` — capability declarations satisfy the existing TIP regression tests.

The tool MUST NOT exit 0 if any of these fail on its own output.

### 7.3 Live-verification gap

Standard #23 §6.4 explicitly accepts `live_verified = False` as valid. The scaffold tool defaults to that. The follow-up live-verification step is OUT of scope for the tool; it produces the issue-text stub but doesn't run the verification.

---

## 8. What's safely generatable vs requires human review

Codifying which parts of the output are mechanical-safe vs need a maintainer's eye:

| Artifact | Confidence | Review required? |
|---|---|---|
| Class skeleton (name, docstring, imports) | HIGH | No |
| `live_verified` marker + docstring "Live status" line | HIGH | No |
| `register(...)` insertion at end of block | HIGH | No |
| Pattern A InjectionPlan (5-line subclass) | HIGH | No |
| OpenRouter-style `_EXTRA_HEADERS` | HIGH | Yes (verify against docs — required headers vary) |
| Pattern B (Responses) `body_transform` rules | MEDIUM | Yes — body constraints are vendor-specific |
| Pattern C (Anthropic) `merge_headers` for anthropic-beta | MEDIUM | Yes — beta sets are version-specific |
| Pattern D (Gemini) URL templating | MEDIUM | Yes — Gemini has multiple URL forms |
| Pattern E SigV4 / OAuth `header_resolver` | MEDIUM | **Mandatory** — auth bugs break in production |
| Pattern F NEW FormatAdapter subclass | LOW | **Mandatory** — wire-shape mismatches silent-fail |
| Pattern G CLI bridge Backend | LOW | **Mandatory** — entire output reviewed line-by-line |
| Capability declarations | MEDIUM | Yes — verify the format actually supports each label |
| Test file structure (class + method names) | HIGH | No |
| Test fixture content (request.json / response.json) | LOW | **Mandatory** — fixtures from docs may be incomplete |
| Docs stub | MEDIUM | Yes — env-var names + troubleshooting need vendor-specific accuracy |
| Cost-fallback tests | HIGH | No |

The scaffold tool annotates every output with one of `# SCAFFOLD-AUTO`, `# SCAFFOLD-VERIFY`, or `# SCAFFOLD-REVIEW` markers indicating expected human attention. CI / linting can grep for `SCAFFOLD-REVIEW` markers to enforce that no merge happens before they're resolved.

---

## 9. Open questions for the implementer

To be resolved before / during implementation:

1. **Doc-fetch implementation**: HTTP client choice (httpx? raw urllib3?) + caching strategy (~/.tokenpak/scaffold-cache/ with TTL?). Suggested: minimal stdlib `urllib.request`, content cached by URL hash, 24h TTL.
2. **LLM-assist mode scope**: which docs structures genuinely benefit from LLM extraction vs simple regex / pattern match? Suggested: only opt-in `--llm-assist` for docs that aren't OpenAPI/markdown-friendly. Out-of-scope for v1.
3. **Where the tool lives in code**: `tokenpak/cli/commands/adapter_scaffold.py`? Or `tokenpak/tooling/scaffold/`? Suggested: under `tokenpak/cli/` since it's a CLI command; tooling for codegen + parsers can live in `tokenpak/cli/commands/_scaffold/`.
4. **Snapshot test format**: golden files vs `assert generated == expected`? Suggested: golden files committed under `tests/scaffold_snapshots/<pattern>/<artifact>` with `--update-snapshots` flag.
5. **Multi-pattern providers**: what if a vendor offers BOTH a Chat Completions endpoint AND a Responses endpoint (like OpenAI)? Scaffold separately or together? Suggested: separately — one PR per family.
6. **CI regression policy**: should generated code be CI-checked the same way hand-written code is? Suggested: yes; the tool is a code generator, not a privileged path.

---

## 10. Acceptance criteria for THIS spec

Per Kevin 2026-04-25:

- [x] Spec references Standard #23 — opening paragraph + every section that codifies a rule.
- [x] Spec uses current merged adapters as examples — every classification node (6.1-6.7) cites worked examples by class name + PR number.
- [x] Spec identifies what can be generated safely vs what requires human review — §8 confidence + review-required matrix.
- [x] Spec includes test strategy — §7 covers tool tests, generated-output tests, and the live-verification gap.
- [x] No implementation work yet — this document is the deliverable; no code has been written.

---

## 11. Companion documents

- [Standard #23 — Provider-Adapter Standard](https://github.com/kaywhy331/obsidian-vault/blob/main/02_COMMAND_CENTER/tokenpak-standards-internal/23-provider-adapter-standard.md) (vault, internal, ENFORCEABLE).
- [Adapter standardization report 2026-04-25](./adapter-standardization-report-2026-04-25.md) (public repo, snapshot-in-time audit).
- [Issue #35](https://github.com/tokenpak/tokenpak/issues/35) — OpenRouter live verification follow-up (the model for live-verified=False handling).
- [Issue #38](https://github.com/tokenpak/tokenpak/issues/38) — OpenRouter cost-table per-slug entries.
- [Issue #39](https://github.com/tokenpak/tokenpak/issues/39) — `tokenpak-claude-code` slug grandfather case.
