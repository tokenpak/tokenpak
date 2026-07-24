# First Measured Savings Receipt

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

2. In terminal 1, start one proxy session with an eligible compression profile
   and the local per-request receipt enabled:

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

## Produce the first eligible request

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

The initial request may correctly save zero because a new conversation has no
compressible history. A later request becomes eligible when it carries safe,
historical narrative outside the protected hot window. TokenPak never
compresses system/developer policy, protected instructions, or the newest two
message items. The first eligible request prints a receipt shaped like:

```text
⚡ TokenPak: -1,234 tokens (31%) | $0.004 saved
```

The token counts come from that request's before/after proxy measurements. The
dollar value is an estimate based on TokenPak's model-pricing table. Values
differ by client, model, and payload.

## Eligibility and alternatives

- `tokenpak demo` is an offline fixture, not proof from your request.
- Short, already concise, code-heavy, or protected prompts may correctly save
  zero tokens. Continue normal work; the first eligible request is the proof.
- Byte-preserved routes are intentionally not rewritten and may report zero
  TokenPak compression savings.
- `--stats-footer` is session-scoped. It prints in the proxy terminal and does
  not modify the provider response.
- `safe` and `transparent` profiles intentionally do not provide positive
  compression-savings proof.
- Already-authenticated Codex OAuth is the zero-key reference route. OpenAI,
  Anthropic, and other SDK/API-key routes remain supported alternatives when a
  user chooses them; their keys and explicit model arguments are not TokenPak
  onboarding requirements.

Stop the foreground proxy with `Ctrl-C` when finished. For other application
integrations, run `tokenpak integrate`, review the detected clients, and apply
only the changes you approve.
