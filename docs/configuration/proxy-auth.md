# Proxy-Level Authentication (`TOKENPAK_PROXY_AUTH_TOKEN`)

`tokenpak serve` listens on a TCP port. By default it trusts every client on
``127.0.0.1`` / ``::1`` and rejects anything else — the long-standing
single-user developer workflow. To open the proxy to a non-localhost client
(LAN, VPN, sidecar container, …) set ``TOKENPAK_PROXY_AUTH_TOKEN`` to a
high-entropy shared secret and require ``Authorization: Bearer <token>`` on
every remote request.

## When to use it

- A second machine (e.g. another developer laptop, a CI runner, a phone) needs
  to hit your local proxy.
- You are running TokenPak behind a reverse proxy on a non-loopback interface.
- You are pre-staging for the future Team-tier RBAC work — the same env var
  is the v1 entry point.

For pure single-user, single-machine operation no change is required and the
gate stays off.

## Configuration

```sh
export TOKENPAK_PROXY_AUTH_TOKEN="$(openssl rand -hex 32)"
tokenpak serve
```

A token is any non-empty string; ``openssl rand -hex 32`` is the recommended
generator (256 bits, hex-encoded, 64 chars).

Rotation is by restart: stop the proxy, change the env var, start again. The
proxy holds no token state on disk.

## Decision tree

| Client IP        | `TOKENPAK_PROXY_AUTH_TOKEN` | `Authorization: Bearer …` | Result |
|------------------|-----------------------------|---------------------------|--------|
| `127.0.0.1`/`::1` | (any)                      | (any)                     | allow  |
| non-localhost     | unset                      | (any)                     | 403 forbidden |
| non-localhost     | set                        | missing or wrong          | 401 unauthorized |
| non-localhost     | set                        | correct                   | allow  |

Token comparison uses ``hmac.compare_digest`` (timing-safe).

## Client usage

When the gate is active, your client must send **two** credentials on every
request:

1. ``Authorization: Bearer <TOKENPAK_PROXY_AUTH_TOKEN>`` — authenticates the
   client to the proxy.
2. ``x-api-key: <provider-key>`` — the upstream provider's own API key (or
   server-side credential injection via the creds router).

Example (Anthropic via curl):

```sh
curl https://your-proxy-host:8766/v1/messages \
  -H "Authorization: Bearer $TOKENPAK_PROXY_AUTH_TOKEN" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":1024,"messages":[…]}'
```

## Security guarantees

- **I5 header-allowlist** — the ``Authorization`` header carrying the proxy
  auth token is stripped before the request is forwarded upstream. The
  upstream provider only ever sees its own credential
  (``x-api-key``/server-injected). This is asserted in
  ``tests/proxy/test_proxy_auth.py::test_path4_…`` against an in-process
  mock upstream.
- **Telemetry identity** — on a successful Bearer match, the request emits
  ``user_id`` = SHA-256 hex of the token into:
    1. the SQLite ``requests`` table (``~/.tokenpak/monitor.db``) via
       ``Monitor.log`` — this is the canonical telemetry-row identity that
       ``tokenpak status`` and downstream rollups read; and
    2. the structured JSON request log (``~/.tokenpak/logs/proxy-*.log``)
       under the ``extra.user_id`` key.
  The raw token is never logged, persisted, or copied between requests.
  Schema migration is additive (``ALTER TABLE requests ADD COLUMN user_id
  TEXT DEFAULT ''``), so existing databases upgrade in place — pre-A6 rows
  have an empty ``user_id``.
- **Timing-safe comparison** — ``hmac.compare_digest`` is used, not ``==``.

## Out of scope (today)

- Per-user / multi-tenant RBAC. This is a single shared secret. RBAC is a
  separate Team-tier initiative built on top of this gate.
- Token rotation infrastructure / secret-manager integration. The env var is
  the store; rotate by restart.
- A CLI subcommand to mint tokens. Operators choose their own generator
  (``openssl rand``, ``python -c 'import secrets; print(secrets.token_hex(32))'``,
  password manager, …).
