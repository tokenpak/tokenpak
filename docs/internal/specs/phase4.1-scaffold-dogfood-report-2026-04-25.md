# Phase 4.1 — Scaffold Dogfood Report

**Date**: 2026-04-25
**Scaffold version**: post-PR #41 (Phase 4.0 MVP) + Phase 4.1 hardening on `feat/phase4.1-scaffold-hardening`.
**Runs**: two providers scaffolded into temp directories, generated outputs verified end-to-end.

---

## What was dogfooded

Two synthetic providers scaffolded against real-world docs URLs:

| Slug | Family | Auth | Endpoint | Notes |
|---|---|---|---|---|
| `tokenpak-fireworks-dogfood` | openai-chat | bearer | `api.fireworks.ai/inference/v1/chat/completions` | Pattern A path; OpenRouter-style extra header (`X-Custom: dogfood`) |
| `tokenpak-example-apikey` | openai-chat | api-key-header | `api.example-vendor.com/v1/chat/completions` | New Phase 4.1 renderer (api-key auth, no Bearer) |

Both runs landed under `/tmp/scaffold-dogfood-*/` (out-of-tree, untracked).

---

## What got verified

### A. Phase 4.0 (MVP) findings — captured + fixed in 4.1

| Issue from 4.0 dogfood | Status |
|---|---|
| Generated standalone file failed `ruff check` (missing `_EnvKeyBearerProvider` import) | ✅ Fixed — generated files are now self-contained Python modules with SPDX header, docstring, and proper imports |
| `live_verified=False` docstring used "an" before consonant-starting env var (e.g. "an `FIREWORKS_API_KEY`") | ✅ Fixed — article picked dynamically based on first letter |
| Next-steps message had generic `pytest tests/test_<vendor>_offline.py` placeholder instead of real filename | ✅ Fixed — actual filename emitted |
| No exact register() patch instructions; just "manually add" | ✅ Fixed — full paste-ready 3-step patch printed |
| Extra headers not asserted in generated tests | ✅ Fixed — when `--extra-header` is passed, generated test file includes a `test_extra_headers_injected` method asserting each |

### B. Standard #23 conformance

| Check | Result |
|---|---|
| Slug matches Standard #23 §1.1 regex | ✅ Validation enforces pre-write |
| Class name follows §1.2 (`<CamelCase>CredentialProvider`) | ✅ |
| Test file naming (`test_<vendor>_offline.py`) per §1.3 | ✅ |
| Capability declaration explicit (inherited from `_EnvKeyBearerProvider`) | ✅ |
| Optional-dependency policy (none in dogfood; no SDK referenced) | ✅ N/A |
| `live_verified=False` default per §6.4 | ✅ |
| Docstring includes "Live status" line | ✅ |

### C. Ruff + lint-imports clean

Both generated provider modules pass `ruff check` standalone (no wrapper required):

```
$ python3 -m ruff check /tmp/scaffold-dogfood-*/fireworks_dogfood.py
All checks passed!

$ python3 -m ruff check /tmp/scaffold-dogfood-apikey-*/example_apikey.py
All checks passed!
```

Both compile cleanly via `py_compile`.

### D. Fixture contract

Request + response fixtures parse as valid JSON. Both carry the `_scaffold_note` SCAFFOLD-VERIFY marker pointing at the docs URL so a maintainer post-review knows to replace placeholder model ids with vendor-specific examples.

```
$ python3 -c "import json; print(json.load(open('/tmp/.../request.json'))['_scaffold_note'][:60])"
SCAFFOLD-VERIFY: replace model id + messages with examples from
```

### E. Docs stub

Markdown stub generated with: title, source URL, slug, status section, required env, optional-dependency section (when applicable), supported-models pointer, live-test curl example, troubleshooting checklist, SCAFFOLD-VERIFY maintainer-fillin block. Length: ~50 lines, immediately usable.

---

## Phase 4.1 deliverables verified

### 1. Self-contained generated module

Pre-4.1, the generated file was a class fragment expecting paste-into-credential_injector. Post-4.1, the file is a complete Python module:

```python
# SPDX-License-Identifier: Apache-2.0
"""Auto-scaffolded credential provider for tokenpak-fireworks-dogfood.

Source docs: https://docs.fireworks.ai/api-reference/post-chatcompletions
...
"""

from __future__ import annotations

from tokenpak.services.routing_service.credential_injector import (
    _EnvKeyBearerProvider,
)


class FireworksDogfoodCredentialProvider(_EnvKeyBearerProvider):
    """FireworksDogfood — OpenAI-Chat-compatible, ``FIREWORKS_DOGFOOD_API_KEY``.
    ...
    """
    live_verified = False
    name = "tokenpak-fireworks-dogfood"
    _UPSTREAM = "https://api.fireworks.ai/inference/v1/chat/completions"
    _ENV_VAR = "FIREWORKS_DOGFOOD_API_KEY"
    _EXTRA_HEADERS = {
        "X-Custom": "dogfood",
    }
```

Lints clean as-is. Drops into `tokenpak/services/routing_service/extras/<vendor>.py` and is imported from `credential_injector.py` either by hand or via `--register`.

### 2. Registration workflow improvement

Both options Kevin enumerated landed:

- **A. Printed exact patch instructions** — emitted on every run, not just first-run. Three numbered steps with copy-paste-ready code blocks for: ① the import, ② the register call, ③ the `__all__` entry.

- **B. Opt-in `--register` flag** — when set, the tool atomically patches `credential_injector.py` in-place using anchor-based insertion. Idempotent on re-run; refuses to patch if anchors aren't found (falls back to manual instructions). Tested with synthetic credential_injector at `tests/test_scaffold.py::TestRegisterPatch`.

Default remains non-destructive — `--register` is an explicit opt-in, fully consistent with Standard #23 §3.

### 3. Pattern A polish

- ✅ Comments improved (live_verified explanation expanded; Standard #23 §6.4 cross-reference)
- ✅ `live_verified=False` rationale spelled out in docstring with action items
- ✅ Follow-up issue text now includes complete acceptance criteria + paste-ready format
- ✅ Extra headers reflected in fixtures (preserved as part of docstring metadata) + asserted in tests

### 4. New renderer: `openai-chat + api-key-header`

Generates a standalone class (no `_EnvKeyBearerProvider` inheritance — that base hardcodes `Authorization: Bearer`). Custom auth header configurable via `_AUTH_HEADER` class attribute (default `api-key`, Azure convention). Mirrors `AzureOpenAICredentialProvider`'s shape from PR #32 but without the cloud-wrapper complexity (static URL, no body-aware routing, no SigV4).

Verified:
- Compiles + lints clean
- Test file asserts `api-key` header injection (not Bearer)
- Extra headers supported

### 5. Regression tests

Six new test categories covering Kevin's enumeration:

| Test class | Coverage |
|---|---|
| `TestConflictSkipBehavior` | First run writes everything; second run skips all (idempotent); partial overlap skips only conflicts |
| `TestAtomicWriteBehavior` | No `.scaffold.tmp` files left after success; dry-run writes nothing to disk |
| `TestDocsStubContent` | Slug present, curl example, optional-deps section conditional on `--optional-dep`, troubleshooting section |
| `TestExtraHeaderHandling` | Headers in provider class, asserted in test file, no extra-header test class when no headers |
| `TestInvalidSlugHandling` | 8 bad slugs rejected (wrong prefix, uppercase, underscores, double-hyphen, etc.); 6 good slugs accepted |
| `TestLlmAssistRefusal` | `--llm-assist` exits 2 with clear "not implemented" message |
| `TestApiKeyHeaderRenderer` | New Phase 4.1 renderer fully tested |
| `TestRegisterPatch` | `--register` flag idempotent + refuses without anchors |

Total scaffold tests: **80** (up from 43 in MVP). All pass.

---

## Acceptance criteria

| Criterion | Status |
|---|---|
| PR #41 merged | ✅ merged into main as commit `c029e13c73` |
| Scaffold dogfood report produced | ✅ this document |
| Manual registration friction reduced or clearly documented | ✅ both A (printed instructions) and B (`--register` flag) |
| No destructive file edits by default | ✅ default is non-destructive; `--register` explicit opt-in |
| No live credentials required | ✅ AST guardrail still enforced; `--llm-assist` deferred |
| No long-tail provider implementation started | ✅ Bedrock generic / Anthropic-on-Vertex / IBM watsonx held |

---

## Friction observed (logged for future passes)

- **Multi-line docstring formatting** is fragile; `textwrap.dedent` interactions cost time during MVP. Phase 4.1 went with explicit line-by-line construction. Future renderers should follow the same pattern.
- **Anchor-based regex insertion** in `_register.py` works but is brittle. If `credential_injector.py` changes its anchor comment, `--register` breaks. Mitigation: explicit `RegisterError` with clear "structure changed" message + manual fallback path.
- **Test isolation under module reloads** (per `tests/deprecations`) was a known issue from prior PRs; scaffold tests dodge it by using `source_format`-style string comparisons rather than `isinstance`.
- **Two separate dogfood runs** were needed (Pattern A + api-key-header) — a future `tokenpak adapter doctor --dogfood` command could automate this cycle.

---

## What was NOT done (per directive)

- AWS Bedrock generic
- Anthropic Claude on Vertex
- IBM watsonx
- Other family/auth combinations beyond Pattern A and Pattern A-prime (api-key-header)
- LLM-driven docs inference (`--llm-assist` still stubbed)

Held for future explicit direction.
