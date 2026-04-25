# Adapter Standardization Report — 2026-04-25

Post-merge audit of the eleven CredentialProviders that landed across PRs #27, #30, #32, #33, and #34. Goal: surface the common adapter shape, repeated patterns, naming + envelope inconsistencies, and identify what `tokenpak adapter scaffold --from-docs` should generate plus what should be codified into the standards directory.

## 1. The eleven providers

Sorted by LOC (cheapest first):

| Class | LOC | InjectionPlan fields | Auth shape | Optional dep |
|---|---|---|---|---|
| `MistralCredentialProvider` | 7 | (inherits) | env-key + Bearer | — |
| `GroqCredentialProvider` | 7 | (inherits) | env-key + Bearer | — |
| `TogetherCredentialProvider` | 7 | (inherits) | env-key + Bearer | — |
| `DeepSeekCredentialProvider` | 7 | (inherits) | env-key + Bearer | — |
| `CohereCredentialProvider` | 16 | (inherits) | env-key + Bearer | — |
| `OpenRouterCredentialProvider` | 16 | (inherits + EXTRA_HEADERS) | env-key + Bearer + Referer/Title | — |
| `AzureOpenAICredentialProvider` | 97 | strip + add + url_resolver | env-key + `api-key` header | — |
| `CodexCredentialProvider` | 112 | strip + add + url_override + body_transform | file JWT (`~/.codex/auth.json`) | — |
| `BedrockClaudeCredentialProvider` | 155 | strip + add + url_resolver + body_transform + header_resolver | SigV4 over body bytes | `boto3` |
| `ClaudeCodeCredentialProvider` | 186 | strip + add + merge | file OAuth (`~/.claude/.credentials.json`) | — |
| `VertexAIGeminiCredentialProvider` | 210 | strip + add + url_resolver + body_transform + header_resolver | OAuth2 access token via ADC | `google-auth` |

The same data plotted as a feature matrix:

```
                     strip  add  merge  url_ovr  url_resolv  body_xform  hdr_resolv
ClaudeCode             ✓    ✓    ✓
Codex                  ✓    ✓             ✓                     ✓
Mistral/Groq/...       ✓    ✓             ✓
OpenRouter             ✓    ✓             ✓
Azure                  ✓    ✓                       ✓
Cohere                 ✓    ✓             ✓
Bedrock                ✓    ✓                       ✓           ✓            ✓
Vertex                 ✓    ✓                       ✓           ✓            ✓
```

Plus a separate axis: **discovery mechanism** (how creds are found):
- **env vars**: Mistral, Groq, Together, DeepSeek, Cohere, OpenRouter, Azure, Bedrock (also reads boto3 chain), Vertex (also reads google-auth ADC chain)
- **file**: ClaudeCode (`~/.claude/.credentials.json`), Codex (`~/.codex/auth.json`)

## 2. Common adapter shape

The pattern that emerged through Phases 1–3 is **3 layers**:

```python
class XCredentialProvider:
    name = "tokenpak-x"

    def _load(self) -> Optional[InjectionPlan]:
        # 1. Discover credentials (env / file / chain).
        #    Return None on graceful missing-creds skip.
        # 2. Compute static plan parts (strip + add + URL).
        # 3. Wire dynamic parts (resolvers) when needed.
        return InjectionPlan(...)

    def resolve(self) -> Optional[InjectionPlan]:
        return _cached_resolve(self.name, self._load)
```

Where `InjectionPlan` has six orthogonal slots that compose:

```python
strip_headers      = frozenset({"authorization", "x-api-key"})  # always
add_headers        = {"Authorization": "Bearer ..."}             # static auth
merge_headers      = {"anthropic-beta": "..."}                   # rarely (Claude only)
target_url_override   = "https://..."                            # static rewrite
target_url_resolver   = lambda body, headers: "..."              # body-aware
body_transform        = lambda body: bytes                       # envelope tweaks
header_resolver       = lambda body, url, method, headers: dict  # SigV4-shape
```

The orthogonality is the key insight: **every provider composes from the same 6 slots**. There is no "Bedrock layer" — Bedrock is just `(url_resolver + body_transform + header_resolver)` simultaneously.

## 3. Repeated patterns

**Pattern A: env-key + Bearer + static URL** (5 providers — Mistral, Groq, Together, DeepSeek, Cohere). Already factored as `_EnvKeyBearerProvider`. ~7 LOC per subclass. The most common shape.

**Pattern B: env-key + Bearer + static URL + extra static headers** (1 provider — OpenRouter). Extension of Pattern A via `_EXTRA_HEADERS = {...}`. ~16 LOC.

**Pattern C: env-key + non-Bearer auth + body-aware URL** (1 provider — Azure). Custom `api-key` header (not Bearer) + URL templated from body's `model` field. No reusable base class yet. ~97 LOC.

**Pattern D: file-OAuth + static URL + (optional) body transform** (2 providers — ClaudeCode, Codex). Read JSON file → extract token → inject. Subtle extras: ClaudeCode merges anthropic-beta; Codex transforms the body. ~112-186 LOC. No shared base class.

**Pattern E: signed-request providers** (2 providers — Bedrock, Vertex). Body-aware URL + body transform + dynamic per-request headers (SigV4 / OAuth token). Both depend on a third-party SDK (boto3 / google-auth), both gracefully skip when the SDK is missing. ~155-210 LOC.

The **5 LOC-7 LOC** providers in Pattern A demonstrate the dev-UX target: drop a 5-line subclass in, get a fully-wired provider. Patterns C/D/E are intrinsically heavier because the wire format genuinely differs.

## 4. Inconsistent naming + envelope handling

**Class naming** — three competing conventions:

| Convention | Examples | Problem |
|---|---|---|
| `<vendor>` | `MistralCredentialProvider`, `GroqCredentialProvider`, `CohereCredentialProvider` | OK when vendor = product |
| `<vendor><product>` | `AzureOpenAICredentialProvider` | OK; "Azure" alone is ambiguous |
| `<platform><family>` | `BedrockClaudeCredentialProvider`, `VertexAIGeminiCredentialProvider` | Implies more come (BedrockMistral, VertexClaude) |
| `<vendor><sub-product>` | `ClaudeCodeCredentialProvider` (Anthropic Claude Code), `CodexCredentialProvider` (OpenAI Codex) | Drops the vendor prefix |

**Slug naming** (the `name = "tokenpak-..."` field):

| Slug | Class |
|---|---|
| `tokenpak-claude-code` | ClaudeCodeCredentialProvider |
| `tokenpak-openai-codex` | CodexCredentialProvider |
| `tokenpak-mistral` | MistralCredentialProvider |
| `tokenpak-groq` | GroqCredentialProvider |
| `tokenpak-together` | TogetherCredentialProvider |
| `tokenpak-deepseek` | DeepSeekCredentialProvider |
| `tokenpak-cohere` | CohereCredentialProvider |
| `tokenpak-openrouter` | OpenRouterCredentialProvider |
| `tokenpak-azure-openai` | AzureOpenAICredentialProvider |
| `tokenpak-bedrock-claude` | BedrockClaudeCredentialProvider |
| `tokenpak-vertex-gemini` | VertexAIGeminiCredentialProvider |

Inconsistencies:
1. ClaudeCode keeps the vendor implicit (it's Anthropic), Codex names the vendor first (`openai-codex`). They disagree on whether vendor goes first.
2. Bedrock and Vertex names imply that other model families on the same platform need their own slug (e.g. when Mistral on Bedrock lands, it'll be `tokenpak-bedrock-mistral`). Class names follow the slug — `BedrockMistralCredentialProvider`, etc.
3. Most providers have no model-family qualifier (Mistral covers all Mistral models; OpenRouter covers ~100 models). Bedrock and Vertex require a qualifier because the wire format depends on the family.

**Envelope handling** — three distinct mechanisms:

| Provider | Body envelope |
|---|---|
| Codex | `body_transform` forces `stream=true`, `store=false`, drops `max_output_tokens` |
| Bedrock | `body_transform` strips `model` (encoded in URL), adds `anthropic_version: "bedrock-2023-05-31"` |
| Vertex | `body_transform` strips `model` + `stream` (both encoded in URL) |
| Azure | No transform — caller's body passes through unchanged |

The shared idea: **strip what the URL encodes, add what the wire requires**. Codex differs slightly because its envelope is value-policy (force flags), not URL-routed. The pattern is similar enough to factor into a helper if scaffolding generates it.

## 5. What `tokenpak adapter scaffold --from-docs <url>` should generate

The scaffold tool is the eventual Phase 4 work. After auditing the eleven providers, here's what it should produce given a docs URL:

**Inputs the tool extracts from docs**:

1. **Auth shape** — `Bearer <token>`, `api-key: <key>`, `Authorization: <signature>`, OAuth flow
2. **URL pattern** — static, templated by model, templated by region, regional + model
3. **Body shape** — OpenAI Chat / OpenAI Responses / Anthropic Messages / Google Generative / vendor-specific
4. **Streaming** — same URL with body flag, separate URL verb suffix, separate URL path
5. **Required env / config** — API key var, project var, region var
6. **Optional Python SDK dep** — boto3 / google-auth / vendor-specific / none

**Outputs the tool generates**:

1. **CredentialProvider class** — the right subclass of `_EnvKeyBearerProvider` (Pattern A) when wire is OpenAI-Chat-compat + Bearer auth + static URL. Otherwise a custom class composed from the InjectionPlan slots.

2. **Test file** matching `tests/test_phase{N}_{provider}.py` convention with the standard skeletons:
   - `TestProviderGating`: env-missing returns None, both-set returns plan
   - `TestUrlResolution`: streaming/non-streaming/region (when body-aware)
   - `TestAuthHeader`: stripped + replaced (when not signed)
   - `TestBodyTransform`: each rule (when applicable)
   - `TestHeaderResolver`: signature shape (when SigV4-style)
   - `TestRegistration`: auto-registered, doesn't displace others
   - With `pytest.importorskip` / per-class `_REQUIRES_<DEP>` marker if optional Python SDK

3. **Docs entry** — single section in the integration docs covering env vars, the supported-model list (linked back to the source docs), and live-verification commands.

4. **Capability declarations** when the wire format is novel enough to require a new FormatAdapter (rare — most fall back to existing `OpenAIChatAdapter` / `AnthropicAdapter` / `GoogleGenerativeAIAdapter`).

The scaffold tool is **decision-tree-shaped**, not template-shaped:

```
docs URL
  ├─ does the auth shape fit Bearer + env key?
  │   yes → _EnvKeyBearerProvider subclass (5 LOC)
  │   no  → custom class
  ├─ is the URL static or body-templated?
  │   static → target_url_override
  │   templated → target_url_resolver
  ├─ does the body need transformation?
  │   yes → body_transform (stripping URL-encoded fields, adding required fields)
  │   no  → omit
  └─ is auth dynamic?
      yes (SigV4 / OAuth refresh) → header_resolver + (boto3 / google-auth) gate
      no → static add_headers
```

The scaffold tool's job is to ask the docs the right questions to walk this tree.

## 6. What needs to be codified into standards

Going into `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/`:

### 6.1 Provider naming convention (new chapter in `02-code-standard.md` or new file)

**Rule**: provider slugs follow the form `tokenpak-<vendor>[-<product-or-family>]` where:
- `<vendor>` is the API host: `mistral`, `groq`, `cohere`, `openai`, `anthropic`, `azure`, `aws`, `google`, etc.
- `<product-or-family>` is added when the vendor offers multiple distinct API surfaces:
  - `azure-openai` (Azure has many AI APIs; we're routing to the OpenAI one)
  - `bedrock-claude` (AWS Bedrock serves Claude here; future `bedrock-mistral`, `bedrock-llama`)
  - `vertex-gemini` (GCP Vertex serves Gemini here; future `vertex-claude`, `vertex-mistral`)
  - `openai-codex` (OpenAI's Codex offering, distinct from the standard OpenAI API)

**Migration notes**:
- `tokenpak-claude-code` is grandfathered (the existing slug) but should be considered an alias for `tokenpak-anthropic-claude-code` going forward. Reason: the slug tells users which vendor they're routing to.

### 6.2 InjectionPlan composition rules

Codify the three-layer pattern as the canonical CredentialProvider shape:

1. **`_load()` returns `Optional[InjectionPlan]`** — `None` when creds aren't discoverable. Never raises — graceful skip is the contract for missing creds / missing optional deps.
2. **`resolve()` is `_cached_resolve(self.name, self._load)`** — every provider goes through the 30s TTL cache.
3. **Optional Python deps are gated at the top of `_load`** — `try: import X; except ImportError: log + return None`. Never crash startup.

### 6.3 Test-file conventions

`tests/test_phase{N}_{name}.py` for provider-pack tests, with class-level test names matching `Test{Aspect}`: `TestGating`, `TestUrlResolution`, `TestAuthHeader`, `TestBodyTransform`, `TestHeaderResolver`, `TestRegistration`. Optional-dep providers use `_REQUIRES_<DEP>` markers (the boto3 / google-auth pattern) to skip cleanly in CI when the lib isn't installed.

### 6.4 Additive-only rule (already saved)

`feedback_tokenpak_additive_only.md` is in auto-memory. Promote to standards directory under `02-code-standard.md` §integration-scripts: tokenpak's installer scripts ADD `tokenpak-*` options alongside user's existing config; never remove providers / wipe fallbacks / replace primaries. Destructive behavior gated behind explicit `--exclusive` opt-in. PR #30 implemented this for OpenClaw; future Cursor / Claude Code / IDE integrations inherit the rule.

### 6.5 Capability declaration as load-bearing data

PR #27 made `FormatAdapter.capabilities` declarations gate the proxy hot path's middleware (compression telemetry, capsule injection). Standardize: any new FormatAdapter MUST declare its capabilities; any new middleware MUST gate on a capability label. This is the runtime mirror of the TIP manifest schema's `required: ["capabilities"]` rule.

### 6.6 Optional-dependency policy

Document the pattern for providers depending on third-party SDKs (boto3, google-auth, etc.):
- Import inside `_load`, catch `ImportError`, log INFO with install hint, return `None`.
- Test module imports the dep with `pytest.importorskip` OR uses module-level `_HAS_<DEP>` flag + `pytest.mark.skipif` decorator on the dependent classes.
- One unconditional test verifies the missing-dep skip path (mock the import).

## 7. Recommendations sorted by leverage

1. **Codify §6.4** (additive-only rule) into `02-code-standard.md` — small docs change, large user-protection value.
2. **Codify §6.1** (provider naming) before adding `bedrock-mistral` / `vertex-claude` so they slot in cleanly.
3. **Build the `_EnvKeyBearer + EXTRA_HEADERS` pattern as the canonical reference example** in the docs site — that's the 80% case for new providers.
4. **Phase 4 scaffold tool** designed against §5's decision tree — not as a code-template engine but as an interactive Q&A walker that asks the right questions per docs URL.
5. **Defer**: a ProviderProtocol-level `_FileTokenProvider` base class for ClaudeCode-style file-read providers. The 2 instances we have aren't enough yet to justify a base class.

## 8. State summary at report time

- Eleven providers registered, all with InjectionPlan composition through documented slots.
- Five InjectionPlan capabilities (`strip_headers`, `add_headers`, `merge_headers`, `target_url_override`, `target_url_resolver`, `body_transform`, `header_resolver`) are stable + documented.
- `feedback_tokenpak_additive_only.md` saved in auto-memory; PR #30 enforces it in code.
- TIP capability declarations + plugin discovery + capability-gated middleware all live on main (PR #27).
- The remaining queued work — Bedrock generic, Anthropic-on-Vertex, IBM watsonx, Phase 4 codegen — has no further substrate dependencies. Each fits cleanly into the patterns above.
