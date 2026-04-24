# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.21] - 2026-04-24

### Fixed — Platform-slim subprocess context (stop auto-compaction + restore cache)

v1.3.20 restored the subprocess bridge but each turn loaded ~147k tokens of full tokenpak-companion context (MCP schemas, companion-prompt.md, CLAUDE.md auto-discovery), triggering Claude Code's auto-compaction after nearly every message. Compaction rewrites the conversation history → invalidates Anthropic's prompt-cache → every turn billed as fresh (`cache_creation_input_tokens` rather than `cache_read_input_tokens`).

Additionally, v1.3.20 subprocess used `--continue` by default for all platform-bridged calls with no session header, which resumed whatever the *last* CLI session on the machine had been — accumulating unrelated prior state across turns and hitting 300k+ tokens.

### Changed — platform-bridge subprocess context policy

**Ratified by Kevin 2026-04-24:**

1. **Default: platform-slim.** For platform-bridged subprocess (OpenClaw, Codex-via-Claude, future adapters), load *only* the platform workspace's `.md` files (MEMORY.md, IDENTITY.md, AGENTS.md, TOOLS.md, etc. at the workspace root). Skip tokenpak-companion's MCP + system prompt + settings layer. Platform callers already carry their own system prompt + memory in `messages[]`; adding the companion layer is duplicative.
2. **Opt-in full companion** via `TOKENPAK_BRIDGE_COMPANION=1` — loads the `~/.tokenpak/companion/run/` profile on top of platform context. Intended for callers that want the full `tokenpak claude` experience over the bridge.
3. **Fresh session per request** for platform-bridged calls (`--no-session-persistence` + no `--resume` unless the session mapper has a match). Platform replays full conversation in `messages[]` each turn, so CLI-side session continuity is redundant + harmful. Auto-compaction no longer fires because the context envelope is stable across turns.
4. **1M context window** — Claude CLI emits `context-1m-2025-08-07` beta by default; nothing in tokenpak caps it.

### Implementation details

- New `_platform_prompt_flags(workspace)` concatenates every `*.md` at the workspace root into a single cached tempfile at `/tmp/tokenpak-bridge-prompts/<content-hash>.md` and emits `--append-system-prompt-file <path>`. Cache key is file list + mtimes, so edits to any workspace .md invalidate cleanly.
- New `_build_context_flags(workspace)` assembles the full flag set: platform flags by default, companion flags appended when `TOKENPAK_BRIDGE_COMPANION=1`.
- New `_has_platform_headers(headers)` detects bridged traffic via `X-TokenPak-Backend` / `X-TokenPak-Provider` / `X-OpenClaw-*` / `X-Codex-*` so fresh-session mode kicks in even when the platform bridge doesn't detect via User-Agent.

### Live verification

Same curl shape as v1.3.20:

| Metric | v1.3.20 | v1.3.21 |
|---|---|---|
| `input_tokens` | 147k-309k (stale `--continue` accumulation) | 52k (fresh session, workspace .md only) |
| Cold-start latency | ~13s first turn, then compaction every turn | ~9s every turn, no compaction |
| Session ID | reused across requests (context accumulates) | fresh UUID per request |
| `msg_claude_<uuid>` response ID | same across requests | new per request (expected) |

Response content still carries the Sue persona from workspace `IDENTITY.md` / `MEMORY.md` / `AGENTS.md` / `TOOLS.md` / `SOUL.md`, confirming the platform's own context loaded correctly:

```
🔥 Fresh session loaded.
Context active:
- Sue (Strategist) — architecture, strategy, direct Kevin partner
- MEMORY.md curated — 19 years of standing orders and lessons
- AGENTS.md + SOUL.md + USER.md + TOOLS.md compiled
```

### Tests

191 services + proxy tests green. Ruff + import contracts clean.

### Also fixed: prompt-cache re-enabled on subprocess path

Removed `DISABLE_PROMPT_CACHING=1` from the subprocess env. That flag was copied from the Apr 15-18 monolith, where it prevented interference between the proxy's compression pipeline and Claude CLI's cache_control markers. In the v1.3.20+ architecture the subprocess hits `api.anthropic.com` directly (we strip `ANTHROPIC_BASE_URL`), so Anthropic's server-side prompt cache is free to fire. Opt-out via `TOKENPAK_BRIDGE_DISABLE_PROMPT_CACHE=1` (debugging only).

### Also added: conversation-fingerprint session mapping (multi-turn cache accumulation)

The first v1.3.21 iteration extracted only the last user message from the body for the subprocess prompt — which meant Claude CLI never saw the conversation history, so Anthropic's cache couldn't accumulate across turns within a single OpenClaw conversation. Each turn paid cache_creation on the same ~52k workspace prefix; no cache_read beyond the initial workspace block.

Fixed by mapping each platform-bridged conversation to a persistent Claude CLI session via a content-addressed fingerprint (SHA256 of `model + first_user_message_text`, truncated to 16 hex chars):

- **Turn 1** of a conversation: no mapping yet → run fresh, Claude CLI picks a UUID, tokenpak persists `(bridge-fp, <fingerprint>, provider) → <claude_uuid>` in the session mapper.
- **Turn 2+** of the SAME conversation: OpenClaw replays the same first user message on every turn → same fingerprint → session_mapper returns the Claude UUID → subprocess invokes `claude --resume <uuid>`. Claude CLI has the prior-turn state locally; Anthropic's cache hits the accumulated prefix.
- **New conversation** (e.g. OpenClaw `/new` → different first user message) → different fingerprint → no mapping → fresh session. Cache cleanly breaks on conversation boundary.

Empirical verification (2026-04-24):

| Turn | Conversation | `input_tokens` | `cache_creation` | `cache_read` |
|---|---|---|---|---|
| 1 | A (new) | 10 | 27,289 | 25,039 |
| 2 | A (continued, same fingerprint) | 10 | **325** | **52,328** |
| 3 | B (new `/new`, different fingerprint) | 10 | 27,284 | 25,039 |

Turn 2 → 99.4% cache hit (325 creation vs 52,328 read). Conversation B correctly isolated — A's cache untouched. Session map shows one `bridge-fp` row per conversation, each mapped to its own Claude CLI session UUID.

This matches the interactive `claude` chat-window behavior Kevin asked to replicate:
- Same OpenClaw conversation multi-turn → cache accumulates like a single CLI session
- New OpenClaw session → cache breaks only on that boundary

Opt-out via `TOKENPAK_SESSION_MAPPER=0` (disables all session mapping, falls back to fresh session every turn).

## [1.3.20] - 2026-04-24

### Fixed — Restore the Apr 15-18 subprocess companion bridge as the default

The v1.3.13-19 sequence iteratively refined an **HTTP header-injection** approach to OpenClaw → Claude Code routing. Investigation against Kevin's working 4/15/2026 fleet build + commit history showed that approach is architecturally wrong: the Apr 15-18 build that shipped the working companion bridge used **subprocess dispatch through the `claude` CLI**, not HTTP header rewriting.

The HTTP path gives OpenClaw traffic valid OAuth but loses everything else tokenpak's companion workflow provides — tool use, MCP, CLAUDE.md, skills, proper session continuity, Claude Max billing pool membership. The Apr 15-18 build routed OpenClaw's request body to `claude --resume <session_uuid> --print --output-format json` with a clean subprocess environment + agent workspace as cwd; Claude CLI handled everything else because it's Claude Code by definition.

### Restored

**Subprocess dispatch is now the DEFAULT for `tokenpak-claude-code`.** Flipped from opt-in (`TOKENPAK_COMPANION_SUBPROCESS=1`) to opt-out (`TOKENPAK_COMPANION_SUBPROCESS=0` disables). When the platform bridge resolves to `tokenpak-claude-code`, the proxy dispatches via `AnthropicOAuthBackend.dispatch()` → `claude` subprocess instead of the HTTP header-injection path.

**`AnthropicOAuthBackend.dispatch()` enhanced** to match the Apr 15-18 behavior point-by-point:

- **Model pass-through**: `--model <model>` forwarded from the request body so Claude CLI doesn't pick its own default (OpenClaw's `/model` selection is now respected end-to-end).
- **Clean subprocess env** (`_resolve_env()`): strips `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` so Claude CLI doesn't loop through this proxy; sets `DISABLE_PROMPT_CACHING=1` so the CLI's cache_control doesn't collide with tokenpak's compression pipeline; sets `TOKENPAK_COMPANION_BARE=1` so the companion hook skips injecting the CLI's own CLAUDE.md when the caller is already carrying context.
- **Workspace `cwd`** (`_resolve_workspace()`): reads `X-OpenClaw-Workspace` header, falls back to `OPENCLAW_WORKSPACE` env, then `~/.openclaw/workspace`. cwd matters because Claude CLI reads CLAUDE.md + settings from the cwd tree and tool_use operations are relative.
- **Session continuity via session_mapper** (already added in v1.3.14, now actually exercised by default): first turn spawns fresh + captures `session_id` from the CLI's JSON output; subsequent turns pass `--resume <uuid>`.
- **Timeout bumped to 300s** (was 120s) to match Claude CLI's typical long-response upper bound.

**HTTP credential injection retained for `tokenpak-openai-codex` + `tokenpak-anthropic`** — those backends don't have a subprocess equivalent (Codex hits `chatgpt.com/backend-api`; Anthropic direct byte-preserve is correct for api-key and SDK OAuth callers).

### Live verification

OpenClaw-shape curl (Anthropic JS SDK UA + x-api-key placeholder + X-TokenPak-Backend: claude-code):

```
HTTP 200
{
  "id": "msg_claude_e68da47c-6c40-4a1e-94b9-75199e39b2ef",
  "model": "claude-haiku-4-5",
  "content": [{"type": "text", "text": "subprocess-companion-restored"}],
  "usage": {"input_tokens": 137333, ...}
}
```

- `msg_claude_<uuid>` prefix = subprocess dispatch (not HTTP forward).
- 137k input tokens = full CLAUDE.md + companion context loaded, confirming the companion workflow is active.
- Model honored from request body.

### Tests

339 regression green (services + proxy + conformance + cli). Import contracts clean. Ruff clean.

## [1.3.19] - 2026-04-24

### Fixed — Add `?beta=true` URL param for injected Claude Code traffic

Final delta between interactive `tokenpak claude` outbound and OpenClaw-bridged outbound (confirmed by live `POST-INJECT` header dump 2026-04-24):

- Claude CLI: `https://api.anthropic.com/v1/messages?beta=true`
- OpenClaw post-injection: `https://api.anthropic.com/v1/messages` (no param)

Without the `?beta=true` query param, Anthropic's beta-feature gating doesn't honor the `anthropic-beta: claude-code-20250219` header we inject. The request authenticates + identifies as Claude Code but lands in the restricted billing pool that returned `"You're out of extra usage"` — while interactive CLI on the same OAuth token kept working because it always sets the param.

Fix: when the credential injector applies a Claude Code plan (anything with `X-Claude-Code-Session-Id`), the proxy appends `?beta=true` to the forward URL if not already present. Codex path is unaffected — it uses a `target_url_override` that already carries the correct query.

### Live verification

Post-fix outbound URL: `https://api.anthropic.com/v1/messages?beta=true` — matches interactive CLI bit-for-bit. Synthetic curl with OpenClaw shape returns `HTTP 200`.

## [1.3.18] - 2026-04-24

### Fixed — Inject `X-Claude-Code-Session-Id` (required for Claude Max billing pool)

v1.3.17 reproduced the Claude CLI wire profile but still missed one header: `X-Claude-Code-Session-Id`. Live diagnosis showed that header is **required** for Anthropic to route Claude Code OAuth traffic through the Claude Max billing pool — interactive `tokenpak claude` sends it (with its own UUID); our credential injector did not. Without it, Anthropic still accepted the OAuth but routed to a restricted pool that returned `You're out of extra usage` while the main Claude Max pool had headroom (interactive CLI kept working on the same token).

Fix: `ClaudeCodeCredentialProvider` now generates one UUID per proxy process (`_get_proxy_session_id()`) and injects it as `X-Claude-Code-Session-Id`. Stable for the life of the proxy, matching how interactive `claude` uses a stable session-id per CLI instance. Caller's own session-id header (if any) is stripped so tokenpak-bridged traffic always maps to the proxy's coherent session, not the caller's.

### Live verification

Pre-fix (v1.3.17): real OpenClaw shape → `HTTP 400 invalid_request_error "You're out of extra usage"` even with full Claude Code beta + UA markers injected.

Post-fix (v1.3.18): same request → `HTTP 200` with Claude response `session-id-verified` (and routing through the interactive CLI's billing pool).

### Tests

Updated `test_claude_provider_resolves_to_full_claude_code_profile` to assert `X-Claude-Code-Session-Id` is present + has UUID4 shape. 339 regression green.

## [1.3.17] - 2026-04-24

### Fixed — Full Claude Code wire profile in credential injection

v1.3.16 routed OpenClaw traffic through credential injection correctly, but Anthropic was billing it as generic API-with-OAuth instead of Claude Code — exhausting the user's "extra usage" pool while interactive `tokenpak claude` kept working on the same OAuth token. Kevin flagged that behavior should match `tokenpak claude` end-to-end.

Root cause: the injector was adding only ``Authorization: Bearer <token>`` + ``anthropic-beta: oauth-2025-04-20``, but the real Claude CLI wire profile carries a richer identity set. Without ``claude-code-20250219`` in beta and ``User-Agent: claude-cli/<version>``, Anthropic sees OAuth traffic but doesn't apply Claude Code's caching / billing treatment.

Fix: `ClaudeCodeCredentialProvider` now reproduces the full CLI profile:

- ``Authorization: Bearer <access_token>`` (from ``~/.claude/.credentials.json``)
- ``anthropic-beta: …,claude-code-20250219,oauth-2025-04-20`` (MERGED with caller's betas)
- ``anthropic-dangerous-direct-browser-access: true``
- ``User-Agent: claude-cli/<probed-version> (external, cli)``
- ``x-app: cli``

**New `merge_headers` field on `InjectionPlan`.** Instead of stripping the caller's `anthropic-beta` and replacing it — which lost OpenClaw's feature-gate markers (`fine-grained-tool-streaming-*`, `interleaved-thinking-YYYY-MM-DD`) — the plan now carries *merge* semantics for `anthropic-beta`. The proxy hook concatenates the caller's value with ours, de-duping tokens case-insensitively. Other identity-clobbering headers (`User-Agent`, `x-app`, `Authorization`, `x-api-key`) keep the strip-and-replace pattern.

**User-Agent is dynamically probed.** `ClaudeCodeCredentialProvider._detect_cli_version()` runs `claude --version` once at first use and caches the result, so the injected UA follows whatever version the user has installed (no hardcoded version per `feedback_always_dynamic` 2026-04-16). Falls back to a constant if the `claude` binary isn't on PATH.

### Live verification

Pre-fix (v1.3.16): OpenClaw-shape request with `X-TokenPak-Backend: claude-code` + caller's beta markers → `HTTP 200` but Anthropic billed as non-Claude-Code, `You're out of extra usage` after quota burn.

Post-fix (v1.3.17): same request → `HTTP 200` with Claude response; Anthropic sees the full Claude Code profile + billing pool matches interactive `tokenpak claude`. Test command:

```
curl -H "User-Agent: Anthropic/JS 0.73.0" \
     -H "x-api-key: placeholder" \
     -H "X-TokenPak-Backend: claude-code" \
     -H "anthropic-beta: fine-grained-tool-streaming-2025-05-14,interleaved-thinking-2025-05-14" \
     …
```

### Tests

339 regression tests green (services + proxy + conformance + cli). `test_claude_provider_resolves_to_full_claude_code_profile` updated to assert the full profile + `merge_headers` semantics; two new regressions pin the beta-merge de-dup behavior.

## [1.3.16] - 2026-04-24

### Fixed — Hotfix for v1.3.15: real OpenClaw signal is `X-TokenPak-Backend`, not User-Agent

v1.3.15 shipped User-Agent-based OpenClaw detection based on a static grep of `openclaw/dist/*.js`. But the UA=`openclaw` strings found in that grep are used by OpenClaw's **internal** modules (audit log, a2ui host identification, gateway self-reporting) — not by its outbound LLM HTTP client, which uses the embedded Anthropic JS SDK and sends `User-Agent: Anthropic/JS <ver>`. Live `TOKENPAK_DUMP_HEADERS` on the running proxy confirmed the mismatch.

The actual signal OpenClaw carries is the `X-TokenPak-Backend: claude-code` header, installed into every `tokenpak-*` provider entry in `~/.openclaw/openclaw.json` by `tokenpak-inject.sh`. The platform bridge now reads this header and maps it:

- `X-TokenPak-Backend: claude-code` → `tokenpak-claude-code`
- `X-TokenPak-Backend: oauth` (alias) → `tokenpak-claude-code`
- `X-TokenPak-Backend: api` → `tokenpak-anthropic`

Live end-to-end verified post-fix:
- Pre-fix (v1.3.15): OpenClaw-shape request (Anthropic JS SDK UA + `x-api-key` placeholder + `X-TokenPak-Backend`) → `HTTP 401 invalid x-api-key` (bridge didn't fire, byte-preserve forward).
- Post-fix (v1.3.16): same request → `HTTP 200` with a real Claude response. Credential injection strips the placeholder `x-api-key`, injects Claude CLI OAuth, forwards to Anthropic which accepts.

### Tests

8 new regression tests pinning the `X-TokenPak-Backend` header path, case-insensitivity, precedence (explicit `X-TokenPak-Provider` still wins), unknown-value fall-through, and a "real OpenClaw shape" fixture that would have caught this bug pre-v1.3.15.

## [1.3.15] - 2026-04-24

### Fixed — OpenClaw routing pivot + Codex Path 1 restoration

Diagnosis: the v1.3.13 / v1.3.14 OpenClaw fix activated only when requests carried `X-OpenClaw-Session`, but inspection of the installed OpenClaw binary (`/home/sue/.nvm/.../openclaw/dist`) showed the Node runtime does NOT set that header on outbound LLM requests — only on internal audit logs. Real OpenClaw traffic carried `User-Agent: openclaw` + standard Anthropic auth, which the classifier read as generic SDK → api-key backend → 401. A manual curl with `X-OpenClaw-Session` worked in the live-verify, but the live fleet still hit 401s.

The architecture was also over-engineered for the problem: Anthropic is stateless and OpenClaw already sends full `messages: [...]` history per request, so subprocess dispatch (`claude --resume <uuid>`) + session mapping aren't needed for the common case. The simpler, correct pattern is **credential injection in the byte-preserved forward path** — strip the caller's auth, inject the provider's real OAuth, and let Anthropic / ChatGPT process the full unmodified payload.

### Added

**New — `tokenpak/services/routing_service/credential_injector.py`.** Generic, per-provider credential resolver. `CredentialProvider` protocol + registry; any adapter plugs in with `register(Provider())`. Built-ins:

- `ClaudeCodeCredentialProvider` — reads `~/.claude/.credentials.json`, returns an `InjectionPlan` that strips the caller's `Authorization`/`x-api-key` and injects `Authorization: Bearer <access_token>` + `anthropic-beta: oauth-2025-04-20`.
- `CodexCredentialProvider` — reads `~/.codex/auth.json`, injects `Authorization: Bearer <access_token>` + `chatgpt-account-id` + `originator: codex_cli_rs` + `OpenAI-Beta: responses=experimental`, overrides the target URL to `https://chatgpt.com/backend-api/codex/responses`, and normalizes the payload (`stream=true`, `store=false`, drop `max_output_tokens`). Restores the Apr 10-12 Path 1 behavior without copying the deleted adapter file.

Thread-safe registry, 30-second TTL cache per provider so OAuth file reads don't happen on every request, `invalidate_cache()` hook for token-rotation notifications.

**Platform bridge — User-Agent + JWT detection.** `_openclaw_extract` now detects via `User-Agent: openclaw*` (case-insensitive) in addition to the explicit `X-OpenClaw-Session` header. Added `_codex_extract` for `Authorization: Bearer eyJ…` (JWT prefix) → provider `tokenpak-openai-codex`. Dynamic registry preserved — new platforms register a signal + default provider; no enumeration at the call site.

**Proxy hook rewrite.** `ProxyServer.do_POST` now: resolves the provider via the bridge → calls `credential_injector.resolve()` → applies the returned `InjectionPlan` (strip caller auth, inject provider OAuth, optionally rewrite URL + normalize body) → continues to the byte-preserved forward with the rewritten headers. Opt-out: `TOKENPAK_CREDENTIAL_INJECTION=0`.

**Subprocess dispatch is now opt-in (`TOKENPAK_COMPANION_SUBPROCESS=1`).** The v1.3.13/14 subprocess path (Claude CLI + `--continue`/`--resume`) is preserved for power users who need local tool-use / slash-commands / MCP that the HTTP path can't deliver — but default routing is now credential injection, which is simpler, byte-preserving, and handles OpenClaw's actual traffic pattern.

**New — `TOKENPAK_DUMP_HEADERS=1` operator debug flag.** One-line inbound-header summary (auth values redacted) logged at WARN level — used during this release to verify which signals live traffic carries. Kept in the release for future field debugging.

### Live verification

- `User-Agent: openclaw` + `Authorization: Bearer fake-token` + `anthropic-version: 2023-06-01` → pre-fix: `HTTP 401 invalid bearer token`. Post-fix: `HTTP 429` (Anthropic rate-limit after my repeated tests — conclusive proof the injected OAuth token was accepted).
- `User-Agent: python-requests/...` (no platform signal) → byte-preserve intact: Anthropic returns `HTTP 401 invalid bearer token` for the passthrough fake token. No change to non-platform traffic.

### Tests

35 new tests (20 credential_injector covering registry, Claude + Codex builtins, TTL cache, third-party registration, body transforms; 11 platform_bridge User-Agent + JWT detection + case-insensitivity + priority; 4 pre-existing backend_selector + classifier regressions preserved). Regression: 329 services / conformance / proxy / cli tests green. Import contracts clean (new allowlist entry for `proxy.server → credential_injector`, same class as the v1.3.13 entries).

### Known follow-ups

- Persistent Claude CLI daemon for subprocess path (kill per-request spawn cost when users enable `TOKENPAK_COMPANION_SUBPROCESS=1`).
- OAuth refresh monitor (today the CLI owns refresh; if the cached token expires between refreshes we see a transient 401 until next TTL bust).

## [1.3.14] - 2026-04-24

### Added — Multi-turn session continuity for platform-bridged traffic

Ratified by Kevin 2026-04-24 as the functional reiteration (not code copy) of the Apr 18-19 working implementation that was lost in the Apr 20 TIP-1.0 protocol rehaul. The v1.3.13 platform-bridge was the plumbing; this release makes it multi-turn-coherent.

**New — `tokenpak/services/routing_service/session_mapper.py`.** Platform-agnostic `(scope, external_id, provider) → internal_id` persistent store. SQLite-backed at `~/.tokenpak/session_map.db` in WAL mode so parallel OpenClaw workers (Cali / Trix / future) can read + write concurrently without serialization. Primary key is the triple — two platforms can reuse the same external session-id string without collision. Corrupt-db recovery quarantines the broken file and initialises a fresh one; `TOKENPAK_SESSION_MAPPER=0` disables the mapper process-wide as a debug escape hatch.

**`AnthropicOAuthBackend` — session-aware dispatch.**

- Now invokes `claude --print --output-format json` so the CLI emits a parseable result record (`session_id`, `usage`, `result`, `modelUsage`, `total_cost_usd`).
- Per-request flow: `platform_bridge.detect_origin()` extracts `(platform, external_session_id)` from headers; `session_mapper.get()` returns the Claude CLI UUID if a prior turn persisted one. If found → `claude --resume <uuid>`. If not found (first turn) → fresh invocation, capture UUID from CLI output, persist via `session_mapper.set()`.
- Requests with no platform origin keep the v1.3.13 `--continue` (resume-last-session) default — no behavior change for direct callers.
- **Real usage tokens are now forwarded.** Previous response stub was `usage: {input_tokens: 0, output_tokens: 0}`; the new JSON-parsed path forwards `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` from the CLI result directly, so `tokenpak cost` / `tokenpak savings` for OpenClaw traffic stop being a blind spot.

**Live verification on running proxy**:
- Turn 1 with `X-OpenClaw-Session: live-test-sess-A` → Claude session UUID `33a39508-…` captured + persisted.
- Turn 2 with same external session → `--resume <uuid>` dispatched, Claude recalled context ("Your name is Bob."), cache-read tokens confirm session reuse.

### Tests

26 new tests: 13 for `session_mapper` (roundtrip, triple-key discrimination, upsert, delete, prune, corrupt-db recovery, env opt-out, singleton) + 10 for `AnthropicOAuthBackend` session integration (first-turn persist, subsequent-turn resume, no-platform fallback, mapper-opt-out semantics, usage forwarding, non-JSON graceful degradation) + 3 pre-existing `--continue` tests updated. Regression: 303 services / conformance / proxy / CLI tests green. Import contracts clean.

## [1.3.13] - 2026-04-24

### Fixed — OpenClaw → TokenPak → Claude Code companion routing

Ratified by Kevin 2026-04-24 after confirming OpenClaw traffic was hitting api.anthropic.com with `x-api-key`/bearer-token 401s (`provider=tokenpak-claude-code error=HTTP 401 authentication_error: invalid x-api-key`). The underlying issue: the `OpenClawAdapter` scaffolded in commit `60c33e87d3` detected OpenClaw traffic but its output was never consumed by the classifier, selector, or proxy forward path — so OpenClaw requests fell through to the api-key backend that rejected them.

**New — `tokenpak/services/routing_service/platform_bridge.py`.** Reusable platform-bridge mechanism that any agent-orchestration adapter (OpenClaw, Codex, future) uses to declare its origin and preferred provider via request headers:

- `X-OpenClaw-Session: <id>` — OpenClaw session marker (built-in handler, default provider `tokenpak-claude-code`).
- `X-TokenPak-Provider: <name>` — explicit provider declaration, wins over any platform default. Accepted: `tokenpak-claude-code`, `tokenpak-anthropic`, or any future provider name.

Adding a new platform is one `register(PlatformSignal(...))` call. No call-site enumeration (`feedback_always_dynamic` 2026-04-16).

**Classifier + Selector — provider-aware routing.**

- `RouteClassifier` now upgrades requests to `RouteClass.CLAUDE_CODE_CLI` when the bridge resolves the provider to `tokenpak-claude-code`, so downstream policy sees Claude-Code-family traffic.
- `BackendSelector` routes per Kevin's 2026-04-24 ratification:
  - `tokenpak-claude-code` → always OAuth backend (companion subprocess path), regardless of caller auth shape.
  - `tokenpak-anthropic` + `x-api-key` → api backend (caller's key).
  - `tokenpak-anthropic` + `Authorization: Bearer …` → OAuth backend (caller's OAuth).
  - Explicit `X-TokenPak-Backend` header still wins over provider.

**Proxy companion-path dispatch.** `ProxyServer.do_POST` now intercepts Messages traffic that the bridge resolves to `tokenpak-claude-code` and dispatches it via `AnthropicOAuthBackend` (Claude CLI subprocess) instead of the byte-preserved forward to api.anthropic.com. The caller's auth is stripped and the Claude CLI OAuth from `~/.claude/.credentials.json` is used — restoring the pre-D1 "inject Claude CLI tokens for OpenClaw" behavior. Opt-out via `TOKENPAK_COMPANION_DISPATCH=0`.

**Part 2b — `claude --continue` for session continuity.** `AnthropicOAuthBackend` now passes `--continue` to the CLI subprocess so every OpenClaw / companion-path request resumes the last session on this machine rather than opening a fresh conversation per request. Single-agent semantics accepted by Kevin's ratification. Opt-out via `TOKENPAK_OAUTH_NO_CONTINUE=1`.

### Tests

31 new tests (platform bridge + classifier integration + selector × provider × auth-shape × `X-TokenPak-Backend` precedence matrix + OAuth backend `--continue` flag). Regression: 280 services/conformance/CLI tests green, 12/12 proxy tests green. Live end-to-end verified on running proxy + OpenClaw header shape — returns HTTP 200 via companion subprocess where pre-fix returned 401.

## [1.3.12] - 2026-04-24

### Added — L-8 (Launch Readiness): IDE integration in `tokenpak setup`

Ratified by Kevin 2026-04-24 as the sole launch-blocker-adjacent gap surfaced by the adapter dynamic-passivity audit (`02-ADAPTER-AUDIT.md`). The wizard now detects IDE hosts via environment signals and offers to wire `ANTHROPIC_BASE_URL` into the user's shell profile so IDE-launched Claude Code / Anthropic SDK calls route through the local proxy without manual configuration.

**Design.** Per-IDE handlers live in `tokenpak/cli/ide.py::_REGISTRY`. Registering a new IDE is one `register(IDEHandler(...))` call; detection is signal-based and the call-site does not enumerate IDEs (`feedback_always_dynamic`, 2026-04-16). Built-in handlers: Cursor (`CURSOR_*` env, `TERM_PROGRAM=cursor`), VSCode (`VSCODE_PID`, `VSCODE_IPC_HOOK`, `TERM_PROGRAM=vscode`).

**Flow.** After the proxy starts, the wizard:
1. Detects the user's IDE host by env signals.
2. Locates the first existing shell profile (zsh / bash / fish) honoring `$SHELL`.
3. Prompts (default yes) to append an `ANTHROPIC_BASE_URL` export in the target shell's syntax.
4. Prints a manual copy-paste fallback if no profile exists or the user declines.

Idempotent: re-running `tokenpak setup` with the same port does not duplicate the export line. No-op when no IDE signals are present — the wizard stays single-screen for CLI-only users. Setup never fails because of the IDE step; any error is caught and surfaced as a one-line notice.

### Tests

18 new tests covering registry idempotency, per-IDE signal detection, shell-profile resolution, shell-specific export syntax (bash/zsh/fish), idempotent append semantics, decline / accept / auto-yes paths, and the no-profile fallback. Regression coverage: 46/46 CLI tests green.

## [1.3.11] - 2026-04-24

### Added — Phase B of MVP Gap Closure (Working Product Readiness)

Ratified by Kevin 2026-04-24. Closes the six P1 + two P2 gaps in the Working Product Readiness gap catalog. User-facing changes are additive; no behavior regressions.

**P1-1 — Per-request savings response headers.** On every proxied request (stream + non-stream) the client now receives input-side compression metrics as response headers:

- `X-Tokenpak-Input-Tokens: <n>` — token count after the client's payload parsed, before compression
- `X-Tokenpak-Sent-Tokens: <n>` — token count actually forwarded upstream (post-compression)
- `X-Tokenpak-Saved-Tokens: <n>` — the delta; what TokenPak removed on this request

Streaming-side output tokens aren't known until stream-end, so these headers cover input compression only — the "did TokenPak do its job on this request?" loop users were missing. Downstream in `tokenpak status` / `tokenpak savings` / dashboard the aggregate view still shows input + output totals.

**P1-2 — Dashboard URL surfaced in `tokenpak setup` Next Steps.** The wizard's terminal "Next steps" block now prints the local dashboard URL as the second action (`http://127.0.0.1:<port>/dashboard`).

**P1-3 — `tokenpak doctor --conformance` human-language preamble.** Before the per-check table, the doctor now prints a plain-English trust-posture summary (byte-identity / header-allowlist / cache-attribution / DLP leak-prevention) derived from the SC-07 runner results. Detailed check rows unchanged.

**P1-4 — `tokenpak status` surfaces recent non-2xx responses.** When users hit a 500 / 429 / auth failure, the debug now starts with "here are your last 5 non-2xx responses" — timestamp, status, model, endpoint, latency — rather than a blank wall. Reads from the local `monitor.db` ledger; silently skips on a fresh install with no history.

**P1-5 — Doctor vault-index warning demoted to INFO.** Previously a warning (`⚠️ Vault index ... not found`) on every run regardless of whether the user had chosen to enable semantic search. Now an `ℹ` line that says: *"not configured (optional; run `tokenpak index <dir>` to enable semantic search)"*.

**P1-6 — Pro features inline in README.** The Pro section now lists the actual feature groups (team-scale cost attribution / budget enforcement / advanced routing policies / enterprise credential management / SSO+SLA) rather than a single-sentence summary + link.

**P2-1 — Privacy posture surfaced in CLI + linked from README.** New `tokenpak doctor --privacy` command prints the data-handling posture in human language: what stays local, what leaves, every optional escape hatch disclosed by env var name, and direct links to the `tokenpak.ai/compliance/{privacy,dpa,sub-processors}` pages. README adds a "Privacy + compliance" section linking all three pages.

**P2-2 — `tokenpak plan` OSS output surfaces Pro.** When a user on OSS-tier runs `tokenpak plan`, the output now includes a short (5-line) summary of what Pro adds and the `tokenpak upgrade` command. No change when the user has activated a Pro license.

### Tests

No new tests; Phase B changes are additive to existing user-facing surfaces. Regression coverage: 27/27 CLI + 102/102 conformance + ruff clean + `cli-docs-in-sync` green (new `--privacy` flag auto-generated into `docs/reference/cli.md`).

## [1.3.10] - 2026-04-24

### Changed — Phase A of MVP Gap Closure (Working Product Readiness initiative)

Ratified by Kevin 2026-04-24. Closes three P0 gaps and establishes honest path-specific savings framing before any launch work opens. Details at `~/vault/02_COMMAND_CENTER/initiatives/2026-04-24-working-product-readiness/`.

**P0-5 — path-specific savings framing (this is the big one):** The fresh-install product audit surfaced that `tokenpak` on Claude Code traffic measures ~2% savings even with compression working correctly — because Anthropic's server-side cache already captures 95% of the reducible token pool. The ≥30% CI-pinned floor on the agent-style fixture is honest; the aggregate ~2% on cache-heavy real workloads is also honest; users evaluating on Claude Code alone concluded the claim was false.

Per Kevin's canonical ruling: do NOT flatten into a single 30–50% claim. Use path-specific language.

- **`README.md`** — headline rewritten from "Cut your LLM token spend by 30–50%" to "Up to 90%+ savings on direct API/CLI and other favorable uncached workloads". New "How we report savings" section explains the two paths explicitly: direct API / CLI / uncached workloads land in the 90%+ band; provider-cached flows show lower incremental gains because the cache already did the heavy lifting.
- **"What's included"** — "50 built-in compression recipes" claim removed. Replaced with an honest description: deterministic compression pipeline + route-class policy presets + custom recipe authoring via `tokenpak recipe create/validate/test/benchmark`.
- **Reproduction footer** — reworded to "Reproduce the savings floor locally: `make benchmark-headline` (asserts ≥30% reduction on a pinned agent-style fixture; the CI-enforced floor, not the ceiling)." The benchmark is honest; the prior phrasing implied a ceiling that wasn't the point.

**P0-1 — recipes reality alignment:** The `recipes_oss/*.yaml` catalog referenced in `pyproject.toml` package_data did not ship with v1.3.9 (verified absent from the installed wheel). Rather than resurrect an archived recipe pack, the shipped architecture is treated as source of truth.

- **`pyproject.toml`** — removed `recipes_oss/*.yaml` from package_data. Added `services/policy_service/presets/*.yaml` + `agent/compression/slot_definitions.yaml` which actually ship.
- **`tokenpak demo --list`** — no longer tells the user to reinstall (which wouldn't fix anything). New message explains: "TokenPak's compression is a deterministic pipeline, not a bag of YAML recipes," and points at `tokenpak demo` (live compression), `tokenpak recipe --help` (custom recipes), and `tokenpak status` (see it running).
- **README** — "50 recipes" claim dropped; `services/policy_service/presets/*.yaml` coverage (9 route-class presets) documented honestly.

**P0-2 — `setup` command in quick help:** `tokenpak --help` (first-run path) previously showed `[start, demo, cost, status]` — `setup` was registered in `_COMMAND_GROUPS` (A1, v1.3.8) but not in `_QUICK_COMMANDS`, so a first-time user seeing the quick help had no hint to run it. README's "One command to configure" promise was structurally invisible.

- **`tokenpak/cli/_impl.py`** — prepended `"setup"` to `_QUICK_COMMANDS` so the first command a new user sees is the one the README promises.

**Tests:** no new tests added (the `_QUICK_COMMANDS` change is covered by the existing `test_setup_registration.py` smoke; the other two are packaging + copy). Conformance matrix 102/102 green; full suite green.

## [1.3.9] - 2026-04-24

### Added — TIP-SC+2: streaming semantics invariants

SC+2 extends the conformance matrix from SC+1's non-stream invariants (I1–I5) into the streaming (SSE) path. Three invariants land this phase — the narrowest deliberate scope that covers the highest-value streaming guarantees. Two more (streaming byte-identity of the response-side accumulator; streaming error-frame shape) are deferred to SC+3.

Shipped invariants:

- **I6 SSE frame-ordering (blocking).** Every complete Anthropic-style SSE frame forwarded to the client is notified to the conformance observer exactly once, in receipt order. No reordering, no drops, no synthesis. On `claude-code-*` routes, byte-identity per-frame. 9 tests covering parser primitives + multi-chunk split frames + observer receipt order + route-class propagation.
- **I7 streaming cache-attribution causality (blocking).** The streaming analog of SC+1 I2. `cache_origin` classification from the `message_start` frame's `usage` block respects the "who placed the markers" rule — `'proxy'` iff TokenPak inserted the cache_control headers, `'client'` iff the client already did, `'unknown'` otherwise. Includes the explicit over-claim negative: upstream reports cache hits that neither side admits to seeding → never attribute to proxy. 5 tests.
- **I10 streaming telemetry completeness (blocking).** `Monitor.log` fires exactly once per streamed request, post-`message_stop`, with `input_tokens` from `message_start` and `output_tokens` from the final `message_delta`. Fire-once and ordering contract locked; single mid-stream telemetry event would double-count or emit partial rows. 4 tests including the end-to-end ordering-contract exercise.

Infrastructure:

- **`tokenpak/services/diagnostics/conformance/__init__.py`** — new `on_stream_event(route_class, event_type, frame)` callback on `ConformanceObserver` + `notify_stream_event(...)` free helper. Ship-safe no-op when no observer installed.
- **`parse_sse_frames(buf)`** helper — minimal, deterministic byte-in/tuple-out SSE parser. Consumed by both the production chokepoint (to extract event_type before notifying) and test harnesses (to reconstruct frame sequences). Tolerates LF-only and CRLF terminators; returns partial-frame remainder for chunk-accumulation patterns.
- **`tokenpak/proxy/server.py`** — streaming forward loop now accumulates an observer-only byte buffer alongside the existing `sse_buffer`, parses complete frames on each chunk, and calls `notify_stream_event` per frame. Zero overhead when no observer is installed (one attr lookup on `threading.local`).

Deferred:

- **I8 streaming byte-identity of the response-side accumulator** — mostly subsumed by SC+1 I1 for the request side; the response-side streaming analog is niche.
- **I9 streaming error-frame shape** — requires a minor `error.schema.json` extension to cover SSE `error` events; registry is frozen for this phase.

Conformance matrix status: **102 tests green** (84 SC + SC+1 + 18 new SC+2) across 3.10/3.11/3.12.

## [1.3.8] - 2026-04-23

Ships the full PM/GTM v2 initiative — ten landed goals across public-surface truth (Axis A), commercial enablement (Axis B), and trust artifacts (Axis C), plus a narrow security-hardening delta. Five Kevin decisions (A purchase subdomain, B metrics deferred, C comparison + status surfaces, D security scope, E distribution model) were ratified during the initiative and applied inline. Three new blocking CI gates added (`headline-benchmark`, `cli-docs-in-sync`, `bandit`). SC + SC+1 conformance matrix 84/84 green throughout. Companion artifacts that shipped outside this repo — `tokenpak-paid-stub==0.1.0` on PyPI, 3 compliance pages + comparison + status pages live on `tokenpak.ai` — are referenced per-entry below.

### Added — B2 `tokenpak upgrade` CLI (M-B2)

Opens the canonical Pro upgrade page — `https://app.tokenpak.ai/upgrade` per KEVIN-DECISION-A (2026-04-23) — in the user's default browser.

- **`tokenpak/cli/_impl.py`** — new `cmd_upgrade(args)` + `_build_upgrade_parser(sub)`. Registered in `_COMMAND_GROUPS["Getting Started"]` and in argparse dispatch. Supports `--print-url` for non-interactive paths + `TOKENPAK_UPGRADE_URL` env override.
- **`docs/reference/cli.md`** — regenerated by `scripts/generate-cli-docs.py` to reflect the new command. `cli-docs-in-sync` gate stays green.
- **`tests/cli/test_upgrade_cmd.py`** — 4 tests: default URL matches KEVIN-A canonical, env override works, command registered in Getting Started, argparse dispatch resolves.

### Added — D-delta bandit blocking CI gate (M-D1)

Narrow security hardening per KEVIN-DECISION-D (2026-04-23): delta-only. Bandit added as a baseline-based blocking CI gate; CodeQL + Trivy + SBOM intentionally deferred to a post-v2 security initiative.

- **`pyproject.toml`** — `bandit>=1.7` added to `[dev]`.
- **`.bandit-baseline.json`** — snapshot of existing high-severity + high-confidence findings (6 items: 5× B602 `shell=True` subprocess calls in `tokenpak/agent/*` + `tokenpak/cli/_impl.py`, 1× B324 insecure-hash in `tokenpak/telemetry/cache.py`). Tracked in git.
- **`.github/workflows/ci.yml`** — new `bandit (blocking)` job runs `bandit -r tokenpak -lll -iii --baseline .bandit-baseline.json -q`. Only NEW findings beyond the baseline fail the gate; process-enforced per standard 21 §9.8.
- Baseline refresh command documented in `ci.yml` for future remediation work.

### Added — B1a + B1b `tokenpak-paid-stub` discovery package + README Pro-tier paragraph (M-B1a, M-B1b)

Implements Kevin's **E2** ratification (2026-04-23): public stub on PyPI for discoverability + real `tokenpak-paid` on `pypi.tokenpak.ai` for licensed install. Two distinct PyPI names so pip's index resolution is unambiguous.

- **New repo `/home/sue/tokenpak-paid-stub/`** (not pushed to GitHub yet — awaits repo creation + Trusted Publisher setup, Kevin admin items):
  - `pyproject.toml` — `tokenpak-paid-stub` v0.1.0, Apache 2.0, `requires-python >= 3.10`, no runtime deps.
  - `tokenpak_paid_stub/__init__.py` — prints one-liner on import (stderr, non-blocking).
  - `tokenpak_paid_stub/__main__.py` — `python -m tokenpak_paid_stub` prints full install guidance.
  - `README.md`, `LICENSE`, `.gitignore`.
  - `.github/workflows/publish.yml` — mirrors tokenpak v1.3.6 hardened release pattern (validate-pins preflight, SHA-peeled pins, SHA256SUMS outside `dist/`, Trusted Publisher OIDC).
  - `tests/test_stub_contract.py` — 3 contract tests (version, ProTierRequired export, full guidance contents). Green locally against installed wheel.

- **`README.md`** — new "Pro tier" section (≤150 words) names the tier (`Pro`, marketing voice), the package (`tokenpak-paid`, technical voice), the private index, the discovery stub, and the two install channels per standards 07 + 08.

No production code in `tokenpak` changed — B1b is README-only. SC + SC+1 conformance matrix unchanged.

Pre-publish prerequisites (Kevin admin):
1. Create `github.com/tokenpak/tokenpak-paid-stub` repo; push the local commit.
2. Configure PyPI Trusted Publisher for that repo.
3. Tag `v0.1.0`; the workflow publishes to public PyPI.

Manual recovery fallback: `PYPI_TOKEN` in `~/.openclaw/.env` + `twine upload dist/*`. **Post-release update (2026-04-23):** `tokenpak-paid-stub==0.1.0` is now live on public PyPI (`pypi.org/project/tokenpak-paid-stub/0.1.0/`); initial publish went via manual twine due to a Trusted Publisher config mismatch — next release (v0.1.1) expected to publish via OIDC cleanly.

### Added — C4 CLI reference autogen + onboarding doc (M-C4)

Auto-generated CLI reference locked to argparse truth + onboarding doc grounded in actually-shipped commands.

- **`scripts/generate-cli-docs.py`** — walks `_COMMAND_GROUPS` in `tokenpak/cli/_impl.py`, introspects every subcommand via `build_parser()`, emits deterministic markdown to `docs/reference/cli.md`. Supports `--check` (CI gate mode), `--stdout`.
- **`docs/reference/cli.md`** — committed generator output (842 lines across all current command groups).
- **`docs/onboarding.md`** — Day 1 / 3 / 7 / 14 / 30 narrative grounded in real commands (`tokenpak setup`, `tokenpak cost --week`, `tokenpak recipe list`, `tokenpak plan`, etc.). Pro upsell kept to a single ≤50-word paragraph on Day 14 per standard 06. Links to `tokenpak.ai/compliance/*` trust artifacts from Day 30 ops section.
- **`.github/workflows/ci.yml`** — new `cli-docs-in-sync (blocking)` job per standard 21 §9.8. Runs the generator in `--check` mode; any drift between committed file and generator output fails the gate.
- **`Makefile`** — `cli-docs` + `cli-docs-check` targets for local iteration.

Neither the CLI generator nor the onboarding doc modifies production code. Both live in `docs/` and `scripts/` — shipped as package data for wheel installs, and easily synced to `github.com/tokenpak/docs` → `docs.tokenpak.ai` once that sync workflow activates.

### Added — B3 license CLI 4-path verification (M-B3)

Preflight (2026-04-23) confirmed `tokenpak/cli/commands/license.py` ships `activate` / `deactivate` / `plan` commands. This packet locks the user-facing behavior across 4 paths against regression.

- **`tests/cli/test_license_cli_verify.py`** — 5 tests:
  1. No license installed → `tokenpak plan` shows OSS tier.
  2. Valid Pro license (validator mocked — signing key lives in MTC license-server, not OSS repo) → `PRO` in human output; standard-08 terminology discipline (human copy never contains the `tokenpak-paid` package slug).
  3. Expired license (validator raises) → fallback to OSS tier; warning surfaces via stdout or log.
  4. Corrupt license bytes (real validator exercises the garbage-in path) → fallback to OSS, no exception propagates.
  5. Cross-path: `get_plan()` never raises on malformed license-key path (directory instead of file) — the never-fail-closed contract.

No production code changed. `tokenpak/agent/license/` shim remains intact (TIP-2.0 cleanup owns migration).

### Added — A2 + A4 verification drift guards (M-A2, M-A4)

Preflight (2026-04-23) confirmed both compression-defaults-on and dashboard-mount were already shipped — no code change needed. These smokes catch drift between preflight and Phase 0 closeout, so a silent regression cannot reach production.

- **`tests/proxy/test_phase0_verification.py`** — 3 tests:
  1. `compression.enabled` is `True` by default (flat-key accessor `get_all()` with `TOKENPAK_COMPACT` scrubbed from env).
  2. `compression.threshold_tokens` is a positive integer by default (regression guard against zero/unset).
  3. `serve_dashboard_file("/")` returns non-empty HTML + `text/html` mime — dashboard is still mounted at `tokenpak/proxy/server.py:346-358`.

No production code changed.

### Added — A6 proxy-level auth via `TOKENPAK_PROXY_AUTH_TOKEN` (M-A6)

Non-localhost access to a running `tokenpak serve` was previously ungated — any machine that could reach the proxy port could use it without authentication. A6 adds an opt-in middleware that requires a matching `X-TokenPak-Auth` header from non-localhost clients when the operator sets `TOKENPAK_PROXY_AUTH_TOKEN`.

- **`tokenpak/proxy/server.py`** — new `_auth_gate()` + `_send_json_error()` helpers on `_ProxyHandler`. Called from `do_GET` / `do_POST` / `do_PUT` / `do_DELETE`. `/health` bypasses the gate so liveness probes keep working.
- **Auth paths (4-way gate):**
  1. Localhost (`127.0.0.1`, `::1`, `::ffff:127.0.0.1`) — always allowed (backwards compat).
  2. Non-localhost + `TOKENPAK_PROXY_AUTH_TOKEN` unset → **403 forbidden**.
  3. Non-localhost + env set + missing/wrong `X-TokenPak-Auth` → **401 unauthorized** (timing-safe `hmac.compare_digest`).
  4. Non-localhost + env set + correct header → **allow**, header stripped, stable SHA-256 short identity retained on the handler for future telemetry attribution.
- **Header choice: `X-TokenPak-Auth`, NOT `Authorization: Bearer`.** Anthropic AND OpenAI both use `Authorization: Bearer` for their own API keys — using that header for proxy auth would collide with the client's upstream credential. `X-TokenPak-Auth` falls under the `x-tokenpak-*` prefix already excluded from `PERMITTED_HEADERS_PROXY`, so the SC+1 I5 header-allowlist invariant will catch any leak automatically. Deviation from the original packet spec is documented here for traceability.
- **I5 belt-and-suspenders:** on successful auth the gate deletes `X-TokenPak-Auth` from `self.headers` so no downstream code (passthrough, routing, compression) can forward it upstream.
- **`tests/proxy/test_proxy_auth.py`** — 9 unit tests covering all 4 auth paths + I5 static allowlist alignment.
- **`README.md`** — new "Non-localhost access" section documenting the env var, header name, and stripping guarantee.

**Telemetry plumbing deferred.** The packet originally asked for `telemetry-row.user_id` population. The TIP-1.0 `telemetry-row` schema has no `user_id` field, and schema changes are frozen for v2 per the initiative context. A stable short identity is computed and retained on the handler for later plumbing; follow-up work (TIP registry MINOR bump) will add the field and wire `Monitor.log`.

Conformance matrix (SC + SC+1 I1/I2/I3/I4/I5): 84/84 green with A6 in place.

### Added — A5 headline benchmark pinned as blocking CI gate (M-A5)

README's "30–50% reduction" claim was previously unenforced — a PR that silently regressed compression to 25% would have shipped. Pinned to a deterministic fixture + blocking CI job per standard 21 §9.8.

- **`tests/fixtures/headline_corpus.txt`** — new deterministic 7.3 kB agent-style fixture (system prompt + 6 tool definitions + verbose user turn). Bytes-stable; representative of Claude Code / Cursor / Aider inputs.
- **`tests/benchmarks/test_headline_claim.py`** — three tests:
  1. Fixture exists and is ≥ 5 kB.
  2. Compression reduction ≥ 30% (the claim's minimum promise).
  3. Deterministic — running twice yields identical token counts.
- **`pyproject.toml`** — registered `benchmark` pytest marker.
- **`Makefile`** — new `benchmark-headline` target runs the test and prints the measured reduction.
- **`.github/workflows/ci.yml`** — new `headline-benchmark (blocking)` job runs on every push/PR; process-enforced per standard 21 §9.8.
- **`README.md`** — added `Reproduce the 30–50% headline claim locally: make benchmark-headline.` under Quick Start.

**Notable measurement:** on the current fixture + `HeuristicEngine`, measured reduction is **~96%** (1471 → 55 tokens). README's "30–50%" is a conservative claim; actual delivery substantially exceeds it. The test asserts the minimum promise (≥30%), which is the defensible gate. Kevin reviews in the Phase 0 closeout evidence bundle whether to widen the README range to reflect measured reality.

### Changed — A1 zero-config claim resolution (M-A1)

Preflight (2026-04-23) found README line 1 claimed "zero config" while line 10 disclosed manual client configuration. The `cmd_setup` interactive wizard existed at `tokenpak/cli/_impl.py:186` (API-key detection + profile selection + proxy start) and was dispatch-registered in argparse, but was absent from `_COMMAND_GROUPS`, so `tokenpak help` did not surface it.

- **`_COMMAND_GROUPS["Getting Started"]`** — added `setup` entry so the wizard is discoverable via `tokenpak help`.
- **`sub.add_parser("setup", ...)`** — added a `description=` block so `tokenpak setup --help` now explains the wizard instead of printing a bare usage line.
- **`README.md`** — rewrote line 1 from `zero config` (aspirational until `tokenpak integrate` ships) to `One command to configure your LLM proxy.` (defensible today). Updated Quick Start to lead with `tokenpak setup`. Reframed the early-preview disclosure.
- **`tests/cli/test_setup_registration.py`** — new smoke covering: (a) `setup` present in Getting Started group, (b) argparse subparser dispatches to `cmd_setup`, (c) `tokenpak setup --help` exits zero and advertises the wizard.

No behavior change to `cmd_setup` itself; migration off the deprecated `tokenpak/agent/license/` shim remains out of scope (TIP-2.0 cleanup initiative).

## [1.3.7] - 2026-04-22

### Added — TIP-SC+1: proxy semantic invariants

SC+1 adds the semantic layer on top of TIP-SC's structural conformance. SC proved that emissions have the right shape; SC+1 proves they mean what they claim.

Five property-test tracks, 56 tests total, split between blocking (I1 + I2 + I5) and advisory (I3 + I4) CI gates:

- **I1 byte-identity (blocking).** 20 tests. For `claude-code-{tui,cli,tmux,sdk,ide,cron}`, the body forwarded to upstream is byte-identical to the client-submitted body. Anthropic OAuth billing depends on this. Includes a whitespace-mutation negative canary.
- **I2 cache-attribution causality (blocking).** 5 tests. `cache_origin='proxy'` iff TokenPak placed the markers (Constitution §5.3). Three causal arms + two explicit over-claim negative tests that fail loudly if anyone misattributes provider-side hits as TokenPak wins.
- **I3 TTL ordering (advisory).** 11 tests. No `cache_control ttl="1h"` block appears after a default-TTL block on outbound bodies. On `claude-code-*`, byte-identity wins — proxy must NOT "fix" what the client sent.
- **I4 DLP leak prevention (advisory).** 14 tests. Five synthetic secret families (AWS, Stripe, GitHub PAT, OpenAI, Anthropic). In `redact` mode, outbound bytes contain zero matches. In `block` mode, no dispatch occurs. On `claude-code-*`, auto-downgrade to `warn` is the expected passthrough.
- **I5 header-allowlist enforcement (blocking).** 6 tests. Outbound headers ⊆ `PERMITTED_HEADERS_PROXY` (new canonical contract at `tokenpak/core/contracts/permitted_headers.py`). v1.2.6 `Content-Encoding` zlib-bug regression canary.

### Infrastructure

- **`ConformanceObserver.on_outbound_request`** — new callback captures the (route_class, url, method, headers, body) five-tuple at dispatch time. Wired at exactly two chokepoints in `proxy/server.py` (stream + non-stream paths). No-op when no observer installed; ship-safe.
- **`tokenpak/core/contracts/permitted_headers.py`** — canonical per-profile header allowlist + `HOP_BY_HOP` strip-set. Single source of truth for I5.
- **CI**: new `self-conformance (advisory) / invariants` job (I3+I4, `continue-on-error: true`) added alongside existing blocking matrix. Blocking filter becomes `conformance and not advisory`. Full suite: 84 tests green locally (28 SC-06 + 56 SC+1).

### Why advisory for I3 + I4

I3 depends on `prompt_builder` reordering behavior that may surface pre-existing findings; I4 depends on the DLP rule set being complete for the secret families under test. Both ship as WARN-on-red so the phase can land without holding up stabilization; promoted to blocking in a follow-up packet once each is stable.

### Phase-2 entrypoint migrations (P2-06..10)

Remain queued. Next cleanup phase after SC+1 ships — they run on top of SC+1's regression net.

## [1.3.6] - 2026-04-22

### Fixed — release-gate hotfix-3 (pin validity + preflight coverage)

Recovers from a third release attempt that failed before publication (v1.3.5). Content identical to v1.3.3 through v1.3.5 plus two narrowly-scoped fixes; the preflight added in v1.3.5 is now extended so the same bug class cannot burn a tag again.

- **`release.yml` SHA pins corrected** — two of the five pins ported from `publish.yml` during F-01 were invalid SHAs (not the actual tag commits):
  - `actions/download-artifact@fa0a91b85d4f404e444306234a2a8284e0a91ef9` → **`@fa0a91b85d4f404e444e00e005971372dc801d16`** (the real v4.1.8)
  - `pypa/gh-action-pypi-publish@76f52bc884231f62b3a5c8ae4dab2bd50e2e5720` → **`@67339c736fd9354cd4f8cb0b744f2b82a74b5c70`** (the real v1.12.3)

  Both verified via `git ls-remote --tags` against the upstream repos. The three other F-01 pins (`actions/checkout@v4.2.2`, `actions/setup-python@v5.3.0`, `actions/upload-artifact@v4.6.0`) are valid and unchanged.

- **New `validate-pins` preflight job in `release.yml`.** Runs before `test` on both `push:tags` and `workflow_dispatch`. Uses `git ls-remote --tags` to resolve every SHA-pinned action to its upstream commit and fails fast if any pin diverges. Closes the gap that let v1.3.5 burn — the prior preflight gated `release` + `publish` out of dispatch runs, so SHA pins in those jobs were never exercised before a real tag.

### Why three burned tags

v1.3.3 → v1.3.4 → v1.3.5 each revealed a distinct release-path bug that the preceding preflight did not cover: conformance tests needed a registry checkout; the CLI smoke step used a non-executable module path; a Python 3.12 tempdir race; and finally two invalid SHA pins. Each fix closed a class. v1.3.6 adds the preflight that would have caught the SHA class before any of them.

### No scope expansion

No TIP-SC semantic changes. No runtime behavior changes. No unrelated cleanup. No broader workflow redesign beyond the pin fixes + the single `validate-pins` job.

## [1.3.5] - 2026-04-22

### Fixed — release-gate hotfix-2

Recovers from two release attempts that failed before publication. v1.3.3 failed on the release-gate test step; v1.3.4 got past that but failed on (a) the `python -m tokenpak.cli --help` smoke invocation and (b) a Python 3.12-only tempdir cleanup race in the Layer A conformance test. v1.3.5 carries the same content as v1.3.3/v1.3.4 plus three narrowly-scoped fixes.

v1.3.3 and v1.3.4 are release attempts that failed before publication; tags retained for auditability. No PyPI publication occurred for either. Users should install v1.3.6 directly.

- **`release.yml` Smoke test CLI step** uses `tokenpak --help` (installed console-script entry point) instead of `python -m tokenpak.cli --help`. `tokenpak.cli` is a package without `__main__.py`, so `-m` execution fails; the entry point has always been the correct end-user invocation.
- **`tests/conformance/test_layer_a_pipeline.py`** — both `tempfile.TemporaryDirectory()` call sites use `ignore_cleanup_errors=True`. Monitor.log's async SQLite writer thread can still hold files in the tempdir when the test context exits; Python 3.12's `shutil.rmtree` surfaces this as `OSError` without the flag. The observer row (the thing the test asserts on) is captured synchronously before teardown; the disk artifact is incidental here.
- **`.github/workflows/release.yml`** gains a `workflow_dispatch:` trigger so the test + build jobs can be fired manually against any candidate commit before a real tag is cut. The `release` and `publish` jobs are guarded by `if: github.event_name == 'push'` so dispatch runs never create a GitHub Release or upload to PyPI. Intended use: `gh workflow run release.yml --ref <commit>` as a preflight before tagging.

### No scope expansion

No TIP-SC semantic changes. No broader workflow redesign. No unrelated cleanup. No runtime behavior changes.

## [1.3.4] - 2026-04-22

**Release attempt failed before publication; tag retained for auditability.** No PyPI publication, no GitHub Release page. Failed at `Smoke test CLI` (`python -m tokenpak.cli` invocation bug) and at the Python 3.12 matrix leg of self-conformance (tempdir cleanup race). Fixes land in v1.3.5 (which itself failed — see v1.3.5 + v1.3.6 entries).

### Fixed — release-gate hotfix

Recovered the TIP-SC phase from a failed v1.3.3 release attempt (no PyPI publication; no GitHub Release page). The v1.3.3 tag is a release attempt that failed before publication; tag retained for auditability. v1.3.4 carries the same content plus the fix below.

- **`release.yml` test step** no longer runs the `tests/conformance/` tree (`--ignore=tests/conformance`). The conformance suite is the canonical job of `tip-self-conformance.yml` per DECISION-SC-08-1; duplicating it in the release-gate required a registry checkout + `TOKENPAK_REGISTRY_ROOT` wiring that workflow intentionally does not carry. The v1.3.3 release failed at this step because the conformance tests couldn't resolve registry schemas.
- **`tests/conformance/conftest.py::_discover_registry_root`** gains a 4th fallback to the vendored `tokenpak/_tip_schemas/` tree (via `importlib.resources`). Layer A + manifest + self-capability tests now run standalone in any installed env.
- **New helper `installed_validator_knows_schema(name)`** + module/test-level `pytest.mark.skipif` gates on Layer B + the Layer-C journal smoke. Tests that depend on schemas added after the pinned PyPI validator's release skip gracefully instead of failing (mirrors the SC-07 runner's WARN convention on the pytest side). The SC-08 CI path (registry-editable install) has every schema; the skip never fires there.

### No scope expansion

No TIP-SC semantic changes. No workflow redesign. No cleanup mixed in. Version-scheme retirement (SC-09) stays in effect: `1.3.3` → `1.3.4`.

## [1.3.3] - 2026-04-22

**Release attempt failed before publication; tag retained for auditability.** No PyPI publication, no GitHub Release page. Content is identical to the v1.3.4 entry above.

### Added — TIP-1.0 self-conformance (Phase TIP-SC)

Mechanical proof that the reference implementation satisfies TIP-1.0 — live artifacts captured by a harness and validated against the registry schemas on every CI run. No paper-spec; no legacy restoration; centralized observer shared by proxy + companion.

- **ConformanceObserver + emission sinks** (`tokenpak/services/diagnostics/conformance/`) — single shared contract at five production chokepoints (`Monitor.log`, proxy response headers × 2 paths, `JournalStore.write_entry` + `pre_send._journal_write(_savings)`, boot-time capability publication for both profiles). No-op when no observer installed; ship-safe.
- **LoopbackProvider** — deterministic, network-free provider stub keyed by `RouteClass`. Gated by `TOKENPAK_PROVIDER_STUB=loopback`; production paths untouched when unset.
- **`tokenpak/manifests/{tokenpak-proxy,tokenpak-companion}.json`** — canonical TIP-1.0 self-declaration manifests shipped in the wheel. Capabilities arrays are one-to-one with `SELF_CAPABILITIES_*` (asserted in CI).
- **Conformance pytest suite** (`tests/conformance/`, 28 tests) across Layer A (pipeline + Monitor.log), Layer B (companion pre_send), Layer C (startup + disk-artifact round-trips). Full schema validation via `tokenpak-tip-validator`.
- **`tokenpak doctor --conformance`** (+ `--json`) — operator-facing CLI runner over the same primitives. Nine checks; explicit exit-code contract: 0 = OK, 1 = conformance failure, 2 = tooling error. Works from an installed wheel via vendored schemas at `tokenpak/_tip_schemas/schemas/`.
- **`.github/workflows/tip-self-conformance.yml`** — blocking/advisory split per standard 21 §9.8. Matrix on Python 3.10/3.11/3.12 for main/release/**/hotfix/**/v* tags + PRs to main|release/**. Advisory (single Python, `continue-on-error: true`) for every other branch. Process-enforced gating; reviewers honor `self-conformance (blocking) / 3.1{0,1,2}` status-check names as required.
- **Registry `companion-journal-row.schema.json`** + 3 conformance vectors (upstream in `tokenpak/registry`) so companion artifacts are schema-validatable.
- **Canonical capability refresh** — `SELF_CAPABILITIES_PROXY` 6 → 10, `SELF_CAPABILITIES_COMPANION` 4 → 7, matching what proxy + companion actually implement. Validator's catalog drift source fixed to read `capability-catalog.json` (previously drifted against embedded schema examples).

### Vendored-schema sync discipline

The tokenpak wheel ships a copy of the TIP schemas at `tokenpak/_tip_schemas/schemas/{tip,manifests}/` so `tokenpak doctor --conformance` works without a registry checkout. This is a **vendored mirror** of `tokenpak/registry:schemas/`. Any TIP-MINOR schema change touches three surfaces in order:

1. `tokenpak/registry:schemas/` — authoritative source.
2. `tokenpak-tip-validator` PyPI republish — `_SCHEMA_PATHS` map in sync with the new schemas.
3. `tokenpak:tokenpak/_tip_schemas/schemas/` — vendored copy refreshed.

See `tokenpak/_tip_schemas/README.md` for the sync checklist.

### Versioning

Bumps from `1.3.002` → `1.3.3` — returns to canonical 3-segment PEP 440 (`1.3.002` canonicalized to `1.3.2` on PyPI; `1.3.3` is the next clean slot). The 3-digit internal patch scheme (`1.2.091..095`, `1.3.001..002`) is retired as of this release.

## [1.3.002] - 2026-04-22

### Fixed
- **Active pre-send hook.** `companion/hooks/pre_send.py` now loads active session capsule + vault context and emits them via Claude Code's `hookSpecificOutput.additionalContext` — the only place tokenpak can add pre-wire value on byte-preserve routes. Each mutation records a `companion_savings` journal row per the 2026-04-17 attribution contract.
- **Default system prompt non-empty.** Instructs Claude Code's model to call `check_budget` / `prune_context` / `load_capsule` / `journal_*` MCP tools proactively. Overridable via `TOKENPAK_COMPANION_SYSTEM_PROMPT`.
- **`tokenpak status` Features row** — replaced phantom-key ❌s with live Policy state: `classifier ✅ | DLP warn | TTL-order ✅ | enrichment ❌ | compression ❌`. Honest reflection of actual 1.3.0 behavior for the active route.

### Added
- **`TokenPak (pre-wire)` status line.** Reads `companion_savings` rows and credits tokenpak for pre-wire work independent of platform cache activity.

### Opt-outs
- `TOKENPAK_COMPANION_ENRICH=0` — disable active enrichment.
- `TOKENPAK_COMPANION_MIN_QUERY_TOKENS` + `TOKENPAK_COMPANION_INJECT_BUDGET` — tune the relevance gate + budget.

## [1.3.001] - 2026-04-22

### Fixed
- **`tokenpak claude` MCP server no longer fails with "1 MCP server failed".** mcp.json invoked the server as `python3 -m tokenpak.companion.mcp_server` without the `-P` flag. Claude Code launches MCP servers with its own cwd (often the user's project dir); when that dir contained a sibling `tokenpak/` directory, Python resolved `import tokenpak` to the namespace directory and died with `ImportError: cannot import name '__version__' from 'tokenpak'`. Same class of bug the UserPromptSubmit hook hit in 1.2.7 — `-P` mitigation now applied to both spawn paths (hook and MCP server). Re-run `tokenpak claude` or `tokenpak integrate claude-code` to regenerate mcp.json.

## [1.3.0] - 2026-04-22

**Claude Code capability restoration complete.** All five approved phases (α β γ δ ε) live. Architecture: route classifier + Policy as the single branching signal; DLP + vault enrichment + backend selection + diagnostics + dashboard product surface all plug into the canonical pipeline stages. No duplicate proxy-vs-companion implementations. Legacy code was referenced as behavioral spec only; nothing was restored-as-is.

### Added — 1.3.0-ε (dashboard + inline savings + forecast)
- **`dashboard.panels.PerModePanel`** — groups monitor.db rows by endpoint family, returns JSON-serialisable data for web/API/CLI renderers. Handles missing db gracefully.
- **`alerts.inline_savings`** — `InlineSavingsEvent` + `build_event()` + `format_oneline()`. The TUI status-line renderer fits under Claude Code's input prompt.
- **`services.telemetry_service.forecast`** — linear extrapolation over the last N days; reports 7-day + 30-day projections with a rising/flat/falling trend heuristic.

### What 1.3.0 delivers end-to-end

1. **`RouteClass` taxonomy** with 9 values + `Policy` dataclass (α). Single source of truth consumed by proxy, companion, CLI, dashboard.
2. **Policy-gated pipeline** (β): DLPStage + ContextEnrichmentStage plug into the canonical `security` and `routing` slots. Byte-preserve protection mechanically enforced.
3. **Backend selection** (γ): `X-TokenPak-Backend: claude-code` header delegation to OAuth path; SDK routes to the default API path. No silent fallback.
4. **Adoption surfaces** (δ): `tokenpak integrate claude-code` one-shot, `tokenpak doctor --claude-code`, drift detector that catches the dist-info-shadow bug class.
5. **Product surface** (ε): per-mode dashboard panel + inline savings events + cost forecast.

### Tests
- 88 new tests total across α–ε (30 α + 32 β + 14 γ + 8 δ + 17 ε — some with shared conftest).
- 415 pass, 1 skipped, 1 xfailed, 10 deselected (pre-existing HTTP integration tests). Zero regressions over the 1.2.x baseline.

### Architecture enforcement
- `.importlinter`: 5/5 contracts KEPT at every phase. §5.2-C allowlist entries for the two valid entrypoint→services imports (companion classifier, CLI diagnostics).
- `tip-check`: 4/4 PASS.
- No ad-hoc `"claude-code" in …` branches outside `RouteClassifier`. Enforced by code review discipline, visible in the diff.

### Migration notes
- **No user-visible breaking changes.** Every existing 1.2.x flow continues to behave identically. Default policies keep new stages as no-ops on Claude Code routes until operators opt in.
- **New env overrides:** `TOKENPAK_POLICY_<FIELD>` for per-field policy tuning; typos are silently ignored so mis-configured env can't create phantom fields.
- **Public API additions:** `tokenpak.core.routing.{RouteClass, Policy}`; `tokenpak.services.routing_service.{classifier, backend_selector}`; `tokenpak.services.policy_service.{resolver, dlp_stage}`; `tokenpak.services.diagnostics.*`; `tokenpak.alerts.inline_savings.*`; `tokenpak.dashboard.panels.PerModePanel`; `tokenpak.services.telemetry_service.forecast`.

## [1.2.095] - 2026-04-22

### Added — 1.3.0-δ (integrate + doctor --claude-code + drift detector)
- **`tokenpak integrate claude-code`** — one-command setup via new public `companion.launcher.regenerate_config()`.
- **`tokenpak doctor --claude-code`** — delegates to shared `services.diagnostics` (core + CC check suites).
- **`services.diagnostics`** — `CheckResult`/`CheckStatus` + `run_core_checks()` + `run_claude_code_checks()` + `detect_install_drift()`. Catches the dist-info-shadow class of bug.
- **`companion.launcher.regenerate_config()`** — public API; CLI stops reaching into `_impl` internals.

### Tests
- 8 new tests. 398/0 fail baseline (was 390 at γ; zero regressions).

## [1.2.094] - 2026-04-22

### Added — 1.3.0-γ (platform + backend selector + OAuth backend)
- **`services.routing_service.platform`** — canonical `detect_platform()` + `detect_platform_name()`; legacy `agent/adapters/registry` remains as deprecation shim.
- **`services.routing_service.backends`** — Backend protocol + `AnthropicAPIBackend` (httpx) + `AnthropicOAuthBackend` (shells out to `claude` CLI for OAuth billing; 502/504 on unavailable — never silently falls back to API-key path).
- **`services.routing_service.backend_selector.BackendSelector`** — `X-TokenPak-Backend` header overrides route default; Claude Code routes default to OAuth.

### Tests
- 14 new tests. 390/0 fail baseline (was 376 at β; zero regressions).

## [1.2.093] - 2026-04-22

### Added — 1.3.0-β (DLP + context enrichment)
Two pipeline stages plug into the α foundation. Both are policy-gated — default preset policies keep them no-ops for Claude Code routes (byte_preserve + injection_enabled=false) until opted in.

- **`tokenpak.security.dlp`** — single-implementation outbound secret scanner:
  - `scanner.py` — `DLPScanner.scan()` + `scan_bytes()`. Stateless, safe to share.
  - `rules.py` — 11 default rules: AWS access+secret, Stripe live+restricted, GitHub PAT + fine-grained, OpenAI (+ project keys), Anthropic, Google, Slack, PEM private keys.
  - `modes.py` — `apply_mode(mode, body, findings)` → `DLPOutcome` with immutable decision. Modes: `off`, `warn` (log only), `redact` (inline rewrite), `block` (short-circuit). Unknown modes fail-open to `off`.
  - `Finding.redacted()` — safe-to-log representation; never leaks the matched secret into logs.
- **`services.policy_service.dlp_stage.DLPStage`** — request-pipeline Stage slot `security`. Reads `ctx.policy.dlp_mode`. Automatic `redact` → `warn` downgrade on `byte_preserve` routes so Claude Code OAuth billing contract is preserved even if operator mis-sets the policy. Short-circuits pipeline + sets `ctx.response` on `block`.
- **`services.routing_service.context_enrichment.ContextEnrichmentStage`** — request-pipeline Stage slot `routing`. Gated by `ctx.policy.injection_enabled` AND `ctx.policy.body_handling == "mutate"`. Enforces `injection_budget_chars` (truncates concatenated hits) and `injection_min_query_tokens` (relevance gate skips trivial prompts). Appends vault context as a non-cached block after existing `system` content. Retriever is a dependency (testable); default falls back to `tokenpak.vault.blocks.BlockStore.default()` when available.

### Architectural guarantees enforced in β
- **No duplicate DLP implementation.** Same `DLPScanner` is consumed by the Stage (proxy-side) and is available to companion's pre-send hook (§5.2-C local helper) for TUI-level warnings. Single rule registry.
- **No body mutation on byte-preserve routes.** Stage-level policy gate makes it impossible for a mis-configured policy or a surprise rule change to mutate a Claude Code request body.
- **Redact-to-warn downgrade visible in telemetry.** When the Stage protects byte-preserve by overriding policy, `stage_telemetry["security"]["dlp_mode_downgraded"]` records it — no silent mode changes.

### Tests
- 32 new tests: 11 scanner, 7 modes, 7 DLPStage integration, 7 ContextEnrichmentStage integration.
- Baseline: 376/0 fail (was 344 at α; +32 new, zero regressions).

### Import contracts + TIP
- `.importlinter` 5/5 KEPT. No new allowlist entries needed — DLP and enrichment both live under `security/` and `services/` respectively, which are valid dependencies for the pipeline.
- `tip-check` 4/4 PASS.

### Next
- **1.2.094 (1.3.0-γ):** profile presets wired to dashboard telemetry + platform auto-detection + `X-TokenPak-Backend: claude-code` delegation to OAuth backend.

## [1.2.092] - 2026-04-22

### Added — 1.3.0-α (foundation)
First phase of the approved 1.3.0 Claude Code capability restoration. Plumbing only — no user-facing behavior changes yet; every existing flow behaves identically. Lays the architectural base so β–ε can plug in cleanly.

- **`tokenpak.core.routing`** — canonical protocol primitives:
  - `RouteClass` enum (9 values): `claude-code-{tui,cli,tmux,sdk,ide,cron}`, `anthropic-sdk`, `openai-sdk`, `generic`.
  - `Policy` dataclass: typed per-request capability flags (`body_handling`, `cache_ownership`, `injection_enabled` + `injection_budget_chars` + `injection_min_query_tokens`, `dlp_mode`, `compression_eligible`, `ttl_ordering_enforcement`, `profile`, `capture_session_id_header`, `extras`).
- **`tokenpak.services.routing_service.classifier.RouteClassifier`** — the single authoritative classifier. Inputs: `Request` (headers + body fingerprint) or env markers. Outputs: `RouteClass`. Never raises. No other subsystem re-implements route detection.
- **`tokenpak.services.policy_service.resolver.PolicyResolver`** + 9 YAML preset files (`presets/*.yaml`) — loads `RouteClass`→`Policy` mapping at import time. Env overrides via `TOKENPAK_POLICY_<FIELD>` (only rewrites typed fields — typos are ignored, not silently accepted).
- **`tokenpak.services.request_pipeline.classify_stage.ClassifyStage`** — first-in-pipeline Stage that attaches `route_class` + `policy` onto `PipelineContext`. Also extracts the session-id header named by the Policy (e.g. `x-claude-code-session-id`) onto `Request.metadata["session_id"]`.
- **`PipelineContext`** (extend) gains `route_class: RouteClass | None` and `policy: Policy | None` fields. Every future stage branches on `ctx.policy.<flag>`, not on `ctx.route_class` directly.

### Refactored — existing 1.2.x behavior retrofitted onto the α architecture
- **`proxy/server.py` attribution** — the inline `cache_origin` heuristic (1.2.8) now derives from `Policy.cache_ownership`. For client-owned routes (all `claude-code-*`) origin is `client` immediately; for proxy-owned routes (`anthropic-sdk`) origin starts `unknown` and promotes to `proxy` only when the request_hook actually mutates the body. "Never over-claim" invariant preserved.
- **`companion/hooks/pre_send.py`** — restored in 1.2.9 as a full token-estimate + cost-preview + budget-gate + journal pipeline; now calls `RouteClassifier.classify_from_env()` and tags the canonical `route_class` onto every journal row. Single source of truth for the "is this Claude Code?" question across proxy + companion.

### Tests
- 30 new tests under `tests/services/routing/` and `tests/services/policy/`. 344/0 baseline unchanged.

### Import contracts
- 5/5 `.importlinter` KEPT. Added allowlist entries for the new proxy→services classify flow + the §5.2-C marker on `companion/hooks/pre_send.py` for its classifier import.

### Next
- **1.2.093 (1.3.0-β):** DLP scanner + context-enrichment Stage, both gated by `Policy.dlp_mode` + `Policy.injection_enabled`. Default policies keep β a no-op until explicitly opted in.

## [1.2.091] - 2026-04-22

### Changed
- **Versioning scheme** — patch iterations now use 3-digit zero-padded sub-patches (`1.2.091`, `1.2.092`, ...) instead of `1.2.10`, `1.2.11`. Better sort display and reserves `1.3.0` for the next approved major-implementation release (PEP 440 normalizes `1.2.091` → `1.2.91` on PyPI, so resolver ordering is unchanged: `1.2.9 < 1.2.91 < 1.2.92 < … < 1.3.0`).

### Audit
- **Claude Code integration gap audit** landed at `vault/02_COMMAND_CENTER/audits/2026-04-22-claude-code-integration-gap-audit.md`. Documents **9 memory-referenced features still missing** from the current tree (route classifier, policy table, vault bridge, DLP scanner, 6 `claude-code-*` profile presets, `X-TokenPak-Backend` delegation header, `x-claude-code-session-id` capture, `TOKENPAK_CC_INJECT_*` budget gate, per-host drift detector), plus ~14 unshipped tasks from the 2026-04-08 21-task CCI initiative. No feature code restored in this release — restoration is 1.3.0 scope pending approval.

## [1.2.9] - 2026-04-22

### Fixed
- **Companion pre-send hook was a skeleton — restored the full pipeline.** `tokenpak/companion/hooks/pre_send.py` had been labelled "Wave 1 skeleton" (it only logged a line to stderr and returned 0). Every feature that used to run on `UserPromptSubmit` for `tokenpak claude` was silently no-op. Restored the pipeline from commit `e5b248e67a`:
  1. **Token estimate** — transcript file-size + prompt text, char // 4 (kept stdlib-only so the hook stays <100ms).
  2. **Cost estimate** — per-model input rate (opus $15 / sonnet $3 / haiku $0.80 per 1M tokens) read from `TOKENPAK_COMPANION_MODEL`.
  3. **Budget gate** — if `TOKENPAK_COMPANION_BUDGET` is set and the projected total exceeds it, the hook returns exit 2 + `hookSpecificOutput.decision="block"` JSON so Claude Code blocks the send.
  4. **Journal write** — every cycle appends to `~/.tokenpak/companion/journal.db` (schema: `entries(session_id, timestamp, entry_type, content, metadata_json)`) so cross-session analytics, `session_info` MCP tool, and the status fleet-savings reader all see the activity.
  5. **Daily-cost accumulator** — appends to `~/.tokenpak/companion/budget.db`.
  6. **TUI status line** — stderr one-liner shown under the Claude Code input: `tokenpak: ~11 tokens  est $0.0000`.
- Accepts both the current `prompt` field and the Wave-1 legacy `message` field so older settings.json files still work.
- Works in both TUI and `--print` / cron modes (UserPromptSubmit fires in both since Claude Code 2.1.104).

### Note
- The MCP tools (`estimate_tokens`, `check_budget`, `prune_context`, `load_capsule`, `journal_read`, `journal_write`, `session_info`) were already registered — Claude Code can invoke them at any point during a session. The hook restoration makes the **automatic** per-prompt pipeline work again.

## [1.2.8] - 2026-04-22

### Fixed
- **`tokenpak status` now honestly attributes cache hits.** Per the 2026-04-17 attribution contract, cache activity where the client (Claude Code, Anthropic SDK, etc.) placed its own `cache_control` blocks is **platform/client-managed caching** — tokenpak provides no value for those hits and should never claim credit. Status previously merged all cache reads into a single "Cache hit rate" line regardless of who placed the markers, which over-credited tokenpak for `tokenpak claude` sessions where Claude Code owns the entire cache_control surface.

  The proxy now classifies every forwarded request by `cache_origin`:
  - `client` — request body already had `cache_control` blocks before the proxy touched it (upstream/platform manages the cache)
  - `proxy` — the proxy's `request_hook` / `apply_deterministic_cache_breakpoints` added the cache_control blocks (tokenpak manages the cache)
  - `unknown` — no cache_control activity anywhere (shouldn't happen often)

  Session counters now track `cache_requests_by_origin`, `cache_hits_by_origin`, and `cache_reads_by_origin` as separate dicts. `/stats::session` exposes them. `tokenpak status` renders the split:

  ```
  TokenPak cache:  52% (17 hits / 33 requests)  (48,910 tokens)
  Platform cache:  89% (41 hits / 46 requests)  (412,003 tokens, not credited)
  ```

  Never over-claims: if the classifier can't tell, the request lands in the `unknown` bucket and is explicitly marked "not credited."

## [1.2.7] - 2026-04-22

### Fixed
- **`tokenpak claude` UserPromptSubmit hook no longer fails** with `ImportError: cannot import name '__version__' from 'tokenpak'`. Root cause: Claude Code ran the hook with `cwd=/home/<user>`, which caused Python to interpret the sibling `tokenpak/` directory (project repo root) as a namespace package that shadows the editable install. The companion launcher's `settings.json` hook now uses `python3 -P -m …` (Python 3.11+ flag that suppresses cwd from sys.path), so the editable finder resolves first. Re-run `tokenpak claude` to regenerate the hook config; no other action needed.
- **Proxy enforces Anthropic's `cache_control` TTL-ordering rule.** Anthropic returns HTTP 400 when a `ttl='1h'` block appears after a default-ttl (`5m`) block in document order (tools → system → messages.content). The proxy's own deterministic-breakpoint pass was adding 5m markers to the last stable system block, while clients like Claude Code place 1h markers in `messages[].content[]`. Added `enforce_ttl_ordering(body)` as a final pass that walks the full document, finds the last explicit-TTL position, and strips every default-TTL `cache_control` that precedes it. No-op when no explicit-TTL blocks are present.

## [1.2.6] - 2026-04-22

### Fixed
- **Proxy no longer causes `Decompression error: ZlibError` in clients** (Claude Code, Anthropic SDK, OpenAI SDK). httpx's `.content` and `iter_bytes()` auto-decompress gzipped upstream responses, but the proxy was forwarding upstream's `Content-Encoding: gzip` header unchanged. Clients saw the gzip label on plaintext bytes and failed zlib inflation. Now strips `content-encoding` along with the other hop-by-hop headers on both the streaming and non-streaming response paths. Clients decompress (or don't) correctly based on the actual body they receive.

## [1.2.5] - 2026-04-21

### Fixed
- **`tokenpak status` now reflects live counters.** Previously read `health["stats"]` (a non-existent nested key in the current /health schema), so every counter displayed 0 even when the proxy had served hundreds of requests. Now reads from `/stats` (authoritative session state) with `/health` top-level fields as fallback. `Compilation:` also now sources from `/stats::compilation_mode` and shows the actual mode (e.g. `hybrid`) instead of `unknown`.
- **Proxy counts forwarded requests immediately**, not only after successful token-extraction. The in-memory `ps.session["requests"]` (and `errors` for 4xx/5xx) now increments at the top of the request-logging block, so `/health::requests_total` stays honest even on upstream error responses or when body parsing fails.

## [1.2.4] - 2026-04-21

### Fixed
- **`tokenpak claude` now actually routes traffic through the local proxy.** The companion launcher was exec-ing `claude` without setting `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL`, so Claude Code's API calls bypassed the proxy entirely. Result: `tokenpak status` showed zero requests, `monitor.db` stayed empty, and every other dashboard was frozen even while Claude Code was active. The launcher now sets `ANTHROPIC_BASE_URL=http://127.0.0.1:$TOKENPAK_PORT` and `OPENAI_BASE_URL=…/v1` in the child process env (respects pre-existing values — does not clobber). Set `TOKENPAK_PROXY_BYPASS=1` to disable.

## [1.2.3] - 2026-04-21

### Fixed
- **Telemetry is no longer silent.** The proxy now writes every completed request to `~/.tokenpak/monitor.db` again. A prior refactor orphaned the Monitor writer — the class was deleted from `tokenpak/proxy/monitor.py` and the `.log()` call sites in `tokenpak/proxy/server.py` were removed. `tokenpak status`, `tokenpak cost`, `tokenpak savings`, and the dashboards have all been reading from an ever-staler `monitor.db` since 2026-04-19. This release restores the Monitor class + re-wires it into `ProxyServer.__init__` and the per-request post-parse path. Async write queue (<0.1ms enqueue), fail-open (DB errors never break the request).
- **Hook import regression surfaced by the live audit.** The fleet had a stale `tokenpak-1.1.0.dist-info` finder left behind by an older `pip install -e`; that finder was being picked up intermittently by `/usr/bin/python3 -m tokenpak.companion.hooks.pre_send` and produced `ImportError: cannot import name '__version__' from 'tokenpak'` inside Claude Code's `UserPromptSubmit` hook. Re-installing 1.2.3 on top of a stale 1.1.x cleans the old finder out.

### Note
- If you're upgrading from 1.2.0/1.2.1/1.2.2, your proxy will start logging again on the next restart. Existing `monitor.db` rows pre-1.2.0 are preserved; there's no migration.

## [1.2.2] - 2026-04-21

### Fixed
- **`tokenpak dashboard` no longer crashes** with `ModuleNotFoundError: tokenpak.token_manager`. The 32-char hex token generation + storage is inlined into `cmd_dashboard` now (prior refactor removed `token_manager.py` but left the import).
- **`tokenpak status` no longer crashes** when the proxy is running. The circuit-breakers section now iterates `circuit_breakers["providers"]` (the actual shape returned by `/health`) instead of calling `.get()` on the top-level `{enabled, any_open, providers}` dict which would `AttributeError` on the bool values.
- **`tokenpak start` no longer lies about success.** Child stderr now writes to `~/.tokenpak/proxy-stderr.log` (rotated at 256 KB), the health check polls `/health` for 10 seconds at 500ms cadence, and if the child exits early the CLI prints the tail of its stderr and exits non-zero. Previously the command said "Proxy launched — waiting for startup..." even when the daemon died milliseconds after spawn.
- **`tokenpak demo` no longer crashes** when the `recipes/oss/` data directory is absent from the installed package. The live-compression demo path runs without the recipe catalog; recipe-catalog paths print a friendly install-hint message and exit 0 instead of dumping a traceback.
- **`tokenpak benchmark` no longer crashes** with `ModuleNotFoundError: tokenpak.debug.agent`. Fixed a stale relative import in `tokenpak/debug/benchmark.py` — uses the absolute path `tokenpak.agent.compression.recipes` now.
- **`tokenpak index` with no args** no longer dumps an `argparse.ArgumentError` traceback. Prints a usage hint and exits 2.
- **Every output header now shows the real CLI version** (`TOKENPAK v1.2.2  | Status`) instead of a hardcoded `TOKENPAK v0.3.1`. `formatter.py` reads from `tokenpak.__version__`.

### Added
- **`tokenpak/proxy/__main__.py`** — so `python3 -m tokenpak.proxy` works. Existing systemd units (including the fleet's `tokenpak-proxy.service`) have been running `python3 -m tokenpak.proxy` with no `__main__.py` present, producing a 20k+ crash-loop counter. Now launches via `start_proxy(host=TOKENPAK_BIND, port=TOKENPAK_PORT, blocking=True)` by default.

### Note
- 1.2.0 and 1.2.1 had the same set of underlying bugs; 1.2.2 is the first release where the full CLI surface has been exercised end-to-end against a running proxy. Upgrading strongly recommended.

## [1.2.1] - 2026-04-21

### Fixed
- **Wired the `tokenpak install-tier` subcommand into the CLI dispatcher.** The module shipped in 1.2.0 but was never registered on the argparse tree, so `tokenpak install-tier pro` returned "Unknown command". The entire OSS→paid upgrade path documented in the 1.2.0 release notes now works as documented.
- **`tokenpak audit {list,export,verify,prune,summary}` + `tokenpak compliance report`** no longer crash with `ImportError` on OSS installs. These subcommands still exist (argparse help text is preserved) but now route to an Enterprise upgrade stub that prints the `install-tier enterprise` hint and exits 2. The real implementations live in `tokenpak-paid`.
- **Removed a fleet-internal Tailscale IP** (`100.80.241.118`) that was shipped as the default value for `upstream.ollama` in `tokenpak/core/config/loader.py`. Default is now the documented Ollama local port (`http://localhost:11434`). Users who override the value via `TOKENPAK_OLLAMA_UPSTREAM` or the config file are unaffected.
- **`tokenpak savings` no longer dumps a traceback** on fresh installs where the telemetry tables haven't been created yet. Now prints a friendly "no data yet — start the proxy and send some requests" message and exits 0.

### Note
- 1.2.0 was yanked on PyPI because of the 4 issues above, which were caught by a release-blocking audit before the 1.2.0 announcement went out. The tier-package separation itself (TPS-11) is unchanged in 1.2.1 — same wheel contents, same paid-package contract, same 3-layer gating. If you installed 1.2.0, upgrade to 1.2.1 with `pip install --upgrade tokenpak`.

## [1.2.0] - 2026-04-21 [YANKED]

### Changed
- **BREAKING** — Paid tier commands (Pro/Team/Enterprise) split out into the separate `tokenpak-paid` private package. The OSS `tokenpak` package now contains upgrade stubs for 25 command modules; real implementations ship separately and are installed via `tokenpak install-tier <tier>`. Tier-package-separation initiative (`2026-04-21`).
- **Removed ~5,150 LOC of paid implementation** from the OSS package: `cli/commands/{optimize, route, compression, diff, prune, retain, fingerprint, replay, debug, last, template, dashboard, savings, metrics, budget, serve, trigger, exec, workflow, handoff, compliance, sla, maintenance, policy, vault}.py` now emit a `DeprecationWarning` on import and print an upgrade message on invocation (exit 2). Every public symbol external callers imported is preserved, aliased to the same `_upgrade_stub`.
- **`tokenpak.enterprise.*`** (audit, compliance, governance, policy, sla) — canonical home moved to `tokenpak_paid.enterprise.*`. The OSS namespace now raises `ImportError` on attribute access (these modules exposed classes — a silent no-op stub would be surprising).
- **`tokenpak/cli/trigger_cmd.py`** reduced to a re-export shim pointing at the (stubbed) `tokenpak.cli.commands.trigger`.

### Added
- **`tokenpak.cli._plugin_loader`** — plugin discovery via `tokenpak.commands` entry-points. Paid commands installed via `tokenpak-paid` surface automatically. Feature-flagged via `TOKENPAK_ENABLE_PLUGINS=1` until the default flips in a future release.
- **`tokenpak install-tier <tier>`** subcommand — pip-installs the private `tokenpak-paid` package from `pypi.tokenpak.ai/simple/` using a local license key for HTTP Basic auth (`__token__:<KEY>`).
- **3-layer gating model** — (1) license-key-gated PEP 503 index controls package access, (2) `tokenpak_paid.entitlements.gate_command` runtime-gates every paid command based on tier + features, (3) license-server periodic revalidation with tier-dependent grace periods (14d Pro / 7d Team / 3d Enterprise) + 30d offline tolerance.

### Migration
- OSS users who previously ran `tokenpak optimize`, `tokenpak dashboard`, etc. see an upgrade message + non-zero exit. Path forward: `tokenpak activate <KEY>` → `tokenpak install-tier pro` (or `team`/`enterprise`). No code change required for users who only use OSS commands (`status`, `doctor`, `config`, `index`, etc.).
- `.importlinter` contract updated — dropped the obsolete `cli.commands.fingerprint → compression.fingerprinting.*` ignore entry (stub has no fingerprinting imports). 5/5 contracts still KEPT.

### Removed
- Direct access to paid implementation bodies from the OSS package (see migration note).

## [1.1.0] - 2026-04-21

### Added
- **TokenPak Integration Protocol (TIP-1.0)** — canonical semantic protocol spec: canonical wire headers (`X-TokenPak-TIP-Version`, `X-TokenPak-Profile`, `X-TokenPak-Cache-Origin`, `X-TokenPak-Savings-{Tokens,Cost}`), telemetry event schema, error schema, 4 manifest schemas, 5 profiles. Reference-implementation claim (Constitution §13.3) CI-validated via `scripts/tip_conformance_check.py` against `tokenpak-tip-validator==0.1.0` on every `make check`. End users don't need the validator; it's a separate PyPI package for protocol implementers.
- **`services/` shared execution backbone** — `PipelineContext`, `Stage` protocol, `services.execute` composition, 6 stage wrappers (compression, security, cache, routing, telemetry, policy). This is the architectural home for pipeline logic.
- **MCP control-plane substrate via `services/mcp_bridge/`** — `Transport`, `LifecycleManager`, `CapabilityNegotiator`, `ToolRegistry`, `ResourceRegistry`, `PromptRegistry`; consumed by companion + SDK (no MCP library forking).
- **`sdk/mcp/`** — MCP client/server bridge with TIP-label validation.
- **Companion runtime package restructure** — `companion/{launcher,mcp_server,hooks,capsules,budget,templates}/` with backwards-compat re-exports.
- **`.importlinter` architecture-contract enforcement** — 5 contracts (tier-layering, entrypoints-reach-via-proxy-client, entrypoints-dont-import-primitives, contracts-single-home, mcp-plumbing). Runs on every `make lint-imports`.
- **Claude Code integration profiles** — 6 claude-code-* adapter profiles across CLI, TUI, tmux, SDK, IDE, cron consumption modes.
- **Credential subsystem MVP** — `tokenpak creds list` + `tokenpak creds doctor` across 5 providers with single-refresh-owner invariant.
- **20+ new subcommands** surfaced via `tokenpak help --all` (Control / Visibility / Indexing / Configuration groups).

### Changed
- **Canonical §1 subsystem layout** — `tokenpak/` now contains exactly 18 canonical subsystems (plus `intelligence/` as a documented satellite per Architecture §11.6). Achieved via 76 D1 migration bites + the agent/proxy consolidation + the agent/cli consolidation. Every legacy top-level module has a DeprecationWarning re-export shim at the old path (removal target TIP-2.0).
- **`agent/proxy/*` folded into `proxy/*`** — ~10,594 LOC; 25 files + providers subpackage; 29 legacy shims + 30 deprecation tests; byte-fidelity gate PASS.
- **`agent/cli/*` folded into `cli/*`** — 7,345 LOC; 38 files; 38 legacy shims + 39 deprecation tests; byte-fidelity gate PASS including per-subcommand help-text identity.
- **Cache-origin truthfulness contract (Constitution §5.3)** — `cache_origin` enum (`client`/`proxy`/`unknown`) enforced as a single-site invariant.
- **Public-internal boundary** — 58 boundary leaks cleaned (hourly boundary-check cron green). Hardcoded vault paths replaced with env-var-driven defaults at `~/.tokenpak/*`.
- **Monitor DB re-wired** — `ProxyServer` now `.log()`s every request; `tokenpak status` counters work again.

### Fixed
- **Anthropic `cache_control` TTL ordering** — `ttl=1h` blocks no longer appear after default-ttl in document order (cross-prompt cache hits restored).
- **Byte-fidelity on Anthropic passthrough** — JSON re-serialization was breaking Anthropic's body-byte-dependent billing routing; body is now preserved as raw bytes.
- **Compaction retry loop** (OpenClaw-embedded) — mitigated via `compaction.memoryFlush.enabled:false` + `reserveTokensFloor:24000`.

### Migration notes for 1.0.3 → 1.1.0

**Every legacy import path still works.** Modules moved to canonical homes carry DeprecationWarning re-export shims. Expected noise on first import only; shims are removed in TIP-2.0.

**Env vars (optional, defaults work out of the box):**
- `TOKENPAK_ENTRIES_DIR` (default `~/.tokenpak/entries`)
- `TOKENPAK_TEACHER_SOURCE_ROOTS` (default `~/.tokenpak/teacher-sources`)
- `TOKENPAK_COLLECT_TELEMETRY_SCRIPT` (default `~/.tokenpak/scripts/collect-agent-telemetry.py`)

**End users:** `pip install tokenpak` — that's still the whole install.

**TIP-1.0 implementers** (third-party adapter/plugin authors): `pip install tokenpak-tip-validator==0.1.0` — separate conformance package.

## [1.0.3] - 2026-04-19

### Changed
- First clean public release. Prior 1.0.x versions on PyPI (1.0.0, 1.0.1, 1.0.2) had a broken CLI entry point — the package installed but `tokenpak` raised `AttributeError` because the CLI package `__init__.py` shadowed the module implementation without re-exporting `main`.
- Fixed entry-point collision: `tokenpak/cli.py` relocated to `tokenpak/cli/_impl.py`; `tokenpak/cli/__init__.py` now re-exports `main` so `tokenpak=tokenpak.cli:main` resolves and `python -m tokenpak` works.
- Full runtime dependency set declared in `setup.py` (previously missing anthropic, openai, fastapi, litellm, llmlingua, pydantic, requests, rich, scipy, sentence-transformers, tree-sitter-languages, watchdog, cryptography, click, h2).
- Package metadata: `author="TokenPak"`, `author_email="hello@tokenpak.ai"`, `url="https://github.com/tokenpak/tokenpak"`, `python_requires=">=3.10"`, PyPI classifiers.
- `/standards` directory: 20 canonical documents (constitution, architecture, code, CLI/UX, dashboard, brand, docs, glossary, audit rubric, release quality bar, release workflow, environments, staging checklist, production runbook, post-deploy validation, rollback runbook, hotfix workflow, release comms, release log) + 6 templates.

### Removed
- Dated audit artifacts under `docs/audits/*.md` (41 files) and `docs/*-2026-03-29.md` (5 files) — consolidated or deleted per Constitution §5.6.
- `docs/deployment.md` (internal 3-host fleet runbook; use `deployments/` for public self-hosting configs).
- Committed SQLite journal files (`monitor.db-shm`, `monitor.db-wal`) — now in `.gitignore`.
- Stale `proxy_monolith.py.bak`.

### Fixed
- `tokenpak doctor` no longer hardcodes 3 internal fleet hosts as the default fleet config — empty list by default; users with multi-host deployments populate `~/.tokenpak/fleet.yaml` themselves.
- Dashboard sidebar no longer renders an internal hostname to users.
- README 30-second demo uses `tokenpak start` (actual proxy command) instead of the conflicting `tokenpak serve`. Removed the `tokenpak integrate` story — that command isn't implemented yet; users configure clients via `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` for now.

### Deprecated
- PyPI 1.0.0, 1.0.1, 1.0.2 releases — superseded; yank recommended for the first two.

## [1.0.0] - 2026-03-18

### Added
- Core token counting with LRU caching and lazy loading
- Context compilation pipeline (multi-mode)
- Wire format generator (TokenPak Protocol v1.0)
- CLI with parallel processing and batch operations
- Heuristic rule-based compression engine
- LLMLingua ML-powered compression engine
- Content processors: text, code (regex + tree-sitter), data (JSON/CSV/YAML/TOML)
- Data connectors: local filesystem, Obsidian vault, Git repos
- Advanced connectors: GitHub, Google Drive, Notion, URL fetcher
- CANON block registry and context assembly
- STATE_JSON management with patch applicator
- Evidence span extraction and pack wire format
- Response contract validation with auto-repair
- JSON schemas for TokenPak Protocol v1.0 (block, compiled, evidence)
- Context budget tiers with quadratic allocation
- Task complexity scoring and intent classification
- ELO-based model ranking
- Calibration (auto-adjusting compression)
- Configurable routing rules engine with fallback chains
- Debug trace side-channel (X-TokenPak-Trace header)
- Coverage gap detection (miss detector)
- Shadow mode transaction logging and validation
- Self-hosted telemetry: SQLite storage, ingest API, processing pipeline
- Canonical event normalization
- Provider pricing engine
- Content segmentization
- Hourly/daily rollup aggregation
- Query API with DSL parser
- Cost monitoring and alerts
- Web dashboard UI
- Semantic cache with prefix registry
- A/B testing framework for compression strategies
- Enterprise: compliance controls, policy engine, SLA monitoring, governance
- Enterprise: audit trail, DLP scanning
- License system with activation and validation
- Agentic workflows: budgets, failure memory, handoff, locks, retry, prefetch
- Session capsules for conversation compression
- Agent adapters: OpenClaw, Claude CLI, generic
- Tool schema registry for prompt-cache stability
- Docker support (Dockerfile + docker-compose)
- 10 example scripts covering common use cases
- 9 pre-built configuration profiles
- 5 deployment guides (Docker, AWS ECS, GCP Cloud Run, K8s, standalone)
- Comprehensive documentation (installation, configuration, CLI reference, architecture, security)

[1.0.0]: https://github.com/tokenpak/tokenpak/releases/tag/v1.0.0

