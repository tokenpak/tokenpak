# OAuth Routing in the TokenPak Proxy

TokenPak accepts credentials already owned by a supported client. API keys and
OAuth bearer sessions are both supported; neither is a universal TokenPak
requirement, and TokenPak does not choose a model on the client's behalf.

## Auth-type detection

| Header pattern | Classification | Typical use |
|---|---|---|
| `x-api-key: <value>` | `apikey` | Anthropic SDK/API client |
| `Authorization: Bearer sk-...` | `apikey` | OpenAI SDK/API client |
| `Authorization: Bearer <non-sk>` | `oauth` | Codex or Claude Code subscription session |
| none | `none` | rejected on provider-bound routes |

Credential values are used in memory for forwarding and route classification.
They are not written to receipts, telemetry, or logs.

## Codex subscription OAuth

Native Codex sends Responses API requests to its configured
`openai_base_url`. When TokenPak receives `/v1/responses` with a non-`sk-`
OAuth bearer, it:

1. classifies the request as `openai-codex`;
2. decodes the native zstd HTTP entity for safe processing;
3. preserves the client-supplied model and OAuth session;
4. rewrites the upstream URL to
   `https://chatgpt.com/backend-api/codex/responses`; and
5. forwards ordinary JSON without persisting the credential.

The same inbound path with `Bearer sk-...` remains an OpenAI API-key request
and is forwarded to `https://api.openai.com/v1/responses`. Path alone does not
misclassify every Responses request as subscription traffic.

### Recommended launcher path

```bash
tokenpak serve --profile aggressive --stats-footer  # terminal 1
tokenpak codex                                      # terminal 2
```

When the local proxy health check passes, `tokenpak codex` supplies the
invocation-scoped `openai_base_url` override. Codex keeps ownership of its login
and selected/default model. If the proxy is unavailable or the user supplied
an explicit base override, the launcher says that Codex is using its configured
upstream instead of silently claiming TokenPak routing.

No `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, or `--model`
argument is needed for an already-authenticated Codex session.

## Claude Code OAuth

Claude Code subscription users can send a non-`sk-` bearer to `/v1/messages`.
That route remains Anthropic and forwards the client-owned session unchanged:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8766
claude "review this project"
```

The supported Claude Code route is byte-preserved. It provides routing and
telemetry but may correctly show no incremental TokenPak compression savings.

## Optional API-key clients

API-key routes remain available when the user chooses a provider or SDK that
requires one:

```bash
# Anthropic API key — optional Anthropic-specific route
curl http://127.0.0.1:8766/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"your-model","messages":[{"role":"user","content":"hello"}]}'

# OpenAI API key — optional OpenAI-specific route
curl http://127.0.0.1:8766/v1/responses \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "content-type: application/json" \
  -d '{"model":"your-model","input":"hello"}'
```

These are alternatives, not first-run requirements.

## OAuth-specific behavior

- OAuth cache keying is disabled so an expiring session cannot become a stable
  cross-session response-cache identity.
- Token refresh remains the client's responsibility. An upstream 401 is
  returned to the client for re-authentication.
- Responses compression follows the same safety rules as other formats:
  system/developer policy and protected instructions are never capsulized, the
  newest two message items remain hot, and short or nonhistorical requests may
  correctly be ineligible.
- TokenPak receipts record measured request deltas and safe route metadata,
  never bearer values, account IDs, or client session metadata.

## Quick reference

| Client/provider route | Upstream | Auth supplied by | Model selected by |
|---|---|---|---|
| Codex subscription `/v1/responses` | `chatgpt.com/backend-api/codex/responses` | Codex OAuth session | Codex/user config |
| OpenAI API-key `/v1/responses` | `api.openai.com/v1/responses` | SDK/client | SDK/user config |
| Claude Code `/v1/messages` | `api.anthropic.com/v1/messages` | Claude Code OAuth session | Claude Code/user config |
| Anthropic API-key `/v1/messages` | `api.anthropic.com/v1/messages` | SDK/client | SDK/user config |
