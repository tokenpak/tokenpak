# Proposed amendment: unattended Spend Guard contract

Status: **proposed, not ratified**. This branch implements the opt-in contract
below. Adoption of the Spend Guard agent contract and internal-header policy
remains a separate review. The existing approval-scope proposal still applies.

## Unattended launch

An operator or launcher opts in with `TOKENPAK_SPEND_GUARD_UNATTENDED=1` when
starting `tokenpak claude` or `tokenpak codex` through TokenPak's selected proxy.
The launcher sends `X-Tokenpak-Unattended: 1` on requests. Other clients may
send that header directly. It is local proxy metadata and is stripped before
upstream forwarding by the existing internal-header filter.

The proxy does not read this environment variable when serving requests. A
proxy can serve interactive and unattended sessions together. A custom Codex
provider route is outside the launcher's automatic injection; its adapter must
send the header to its TokenPak proxy. Native clients must honor the stop
contract; these source tests do not certify their unattended exit behavior.

## Denial and approval

A denied unattended request returns HTTP 402 with
`error.type=tokenpak_spend_guard_unattended`, `retryable=false`,
`recovery_status=operator_action_required`, `approval_prompt=null`, and
`in_band_approval_allowed=false`. The error includes the original reason,
projected input/output/total tokens, projected cost in USD, pending id and
expiry when a hold could be stored. Estimates are projections, not billed usage.
The agent reports the error and stops; it must not retry, switch providers, or
insert approval text into the conversation.

Unattended holds ignore yes/no and TIP authorization/cancellation. The mode
persists on the hold even if a later request omits the header. An unattended
retry of an interactive hold converts that hold to unattended mode. Interactive
sessions that never opt in keep the existing yes/no flow.

On the proxy host, using the same configured Spend Guard database:

```sh
tokenpak spend-guard events --after 0 --limit 100 --json
tokenpak spend-guard approve tpg_example --json
```

Replace `tpg_example` with the returned pending id. Approval arms that exact
hold and sends no provider request. The supervising adapter may then resume
the original session with the identical held request, model, and API target.
The atomic pending-to-consumed transition permits one release, including
under concurrent retries. Different bodies, sessions, or API targets cannot
redeem it. Approval does not extend expiry or authorize subsequent requests.
Repeated approvals, expired holds, and resolved holds fail with exit code 1.
An unreadable or corrupt local store returns exit code 8.

Hard ceilings cannot be approved, including when a ceiling is tightened after
a hold. A missing session identity, a storage failure, or a denial without a
stored hold reports `pending_id=null` and `operator_approval_available=false`.
The operator must repair the reported condition rather than approving an
invented id. This local CLI uses the host's existing database permissions; it
is not an authentication boundary between an agent and its operating-system
user. Do not expose the operator command to unattended workers as an automatic
approval tool.

## Adapter event contract

The existing `spend_guard_audit` table is the event stream. Adapters read it
through `tokenpak spend-guard events --after EVENT_ID --limit 100 --json`.
The command opens the configured database read-only and does not create it
when absent. It returns `events` in ascending id order and `next_cursor`.
The adapter persists that cursor after durable delivery, deduplicates by
database identity plus `event_id`, and drains pages until empty. IDs are scoped
to one database; a replacement database requires a fresh cursor. Reading does
not acknowledge, delete, approve, or replay anything.

Each event has:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `event_type` | `unattended_escalation` |
| `event_id`, `occurred_at` | Audit row id and Unix timestamp |
| `session_id` | Explicit session attribution; empty when unavailable |
| `pending_id`, `expires_at` | Held-request identity and expiry, or null |
| `reason` | Original policy reason |
| `projected_input_tokens`, `projected_output_tokens`, `projected_tokens` | Token estimates; null when unavailable |
| `projected_cost_usd` | Estimated cost, or null when unavailable |
| `operator_approval_available` | Whether the original denial created an approvable hold |

Events contain no request body, tool output, credentials, headers, or provider
responses. Repeated denials of one hold return the same event id. Denials
without a hold may produce separate events. An event describes the denial at
that time; expiry or a later operator approval can change the hold's state.
On an event write failure, the HTTP response still blocks and explicitly says
`escalation_recorded=false`, `escalation_error=audit_store_unavailable`.
The adapter must report that response directly; it must not assume delivery.

For an Orca/Hub adapter follow-up: associate `session_id` with the adapter's
active Task and Dispatch, durably deliver an escalation carrying the content-free
fields, settle the worker as blocked/failed according to its native protocol,
and wait for a separately authorized operator action before resuming. Do not
infer a task from a process-global session bucket. This branch neither calls
the Hub nor installs an adapter, scheduler, watcher, or approval service.

## Required checks

Unattended denial and event export; no conversational approval, including TIP
and a dropped header; one exact operator-approved retry; concurrent redemption;
expiry; immutable hard ceiling; session and target separation; original reason
on repeated blocks; interactive approval regression; internal header stripping;
launcher opt-in; content-free event fields; explicit event-storage failure.
