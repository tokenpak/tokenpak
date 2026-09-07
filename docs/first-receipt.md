# First Measured Request Receipt

This is TokenPak's supported reference path from a clean install to a measured
receipt from your own real request. Its reference target is three shell
commands and no more than five minutes.

## Before you start

Have these available:

- Python 3.10 or newer.
- A supported client that is already authenticated. The reference path uses
  Codex with its existing OAuth login.
- A real project with enough context for a multi-turn request.
- Outbound provider access. Real usage may count against a subscription or
  incur provider charges.

An API key and an explicit model override are **not requirements**. TokenPak
preserves the authentication and model selection owned by the client. If you
choose an SDK or provider that requires an API key, that key is an optional
client-specific alternative and is forwarded without being persisted.

## The three commands

1. Install TokenPak:

   ```bash
   python -m pip install tokenpak
   ```

2. In terminal 1, start one proxy session with the local per-request receipt
   enabled:

   ```bash
   tokenpak serve --profile aggressive --stats-footer
   ```

3. From the real project in terminal 2, launch the already-authenticated client:

   ```bash
   tokenpak codex
   ```

   Do not add an API key or `--model` unless you intentionally want to override
   the client's normal choices. `tokenpak codex` routes through the local proxy
   when its health check passes; otherwise it states that the client is using
   its configured upstream.

## Produce the first measured request

Inside that Codex session, ask for a substantive project review, for example:

```text
Review this project for release readiness. Read the relevant project files and
give me a detailed, evidence-based assessment.
```

Then continue the same topic:

```text
Turn that assessment into a prioritized release checklist with owners and
verification steps.
```

The proxy prints a receipt shaped like:

```text
⚡ TokenPak: 0 tokens saved
```

That zero is the expected contract for this unmodified reference request. The
built-in Pak builder preserves every system, user, and assistant conversation
turn regardless of its age, length, or selected profile. With no other request
transform configured, the ledger records equal raw and sent token counts, with
zero actual and would-have-saved tokens.

## What this proves and where compression lives

- This is a real request receipt proving local routing, upstream completion,
  ledger persistence, and truthful zero-savings attribution.
- `tokenpak compress <file>` is the supported explicit local compression path;
  it reports before/after counts without sending the content to a provider.
- Companion `prune_context` and `POST /tpk/v1/compress` explicitly reduce
  caller-selected verbose text and report their measured token reduction. They
  are separate from provider-request savings receipts.
- `tokenpak demo` is an offline fixture, not proof from your request.
- `--stats-footer` is session-scoped. It prints in the proxy terminal and does
  not modify the provider response.
- A more aggressive profile does not override role-bearing conversation
  preservation.
- Already-authenticated Codex OAuth is the zero-key reference route. OpenAI,
  Anthropic, and other SDK/API-key routes remain supported alternatives when a
  user chooses them; their keys and explicit model arguments are not TokenPak
  onboarding requirements.

Stop the foreground proxy with `Ctrl-C` when finished. For other application
integrations, run `tokenpak integrate`, review the detected clients, and apply
only the changes you approve.
