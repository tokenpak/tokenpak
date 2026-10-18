# Proposed amendment: Spend Guard approval scope

Status: **proposed, not ratified.** This records a contract change that the
approval-scope fix in this branch needs. The contract owner decides whether
to adopt it. Excluded from the docs site build.

## Problem

The Spend Guard agent contract's **Yes-grant scope** clause says a positive
reply grants approval for a TTL window rather than the single held request,
and its test-coverage requirement asks for "one POSITIVE intent covering
subsequent held requests in the turn". The **Count grants** subsection extends
the same grant with `allow=N`.

No grant table ships in the current source; it was removed when the release
line was re-anchored. What remained was an accidental version of that
behaviour. The approving `yes` stays the last user-role message while an
agent's tool loop adds calls and results after it, and every later held
request in the turn read it as a fresh approval. In one recorded turn this
released nine further held requests within five minutes.

## Proposed text

Replace the opening paragraph of **Yes-grant scope** with:

> **Approval scope.** A typed positive reply releases exactly one held
> request. A reply counts only when it is the newest input item of the
> request and it answers the current hold: a re-send of a request that was
> already held, or of a reply that already released or discarded a hold in
> the same session, is re-prompted (`stale_reply`) and resolves nothing.
> Later over-threshold requests in the same turn are held and each needs its
> own reply. `[TIP: allow=once]` keeps single-request semantics. A
> multi-request grant, if reintroduced, must be opt-in and explicit
> (`[TIP: allow=session ...]` or `allow=N`), never inferred from a bare `yes`.

Add to **Yes/No intent (interactive)**:

> The reply must be the newest input item. When the newest item is anything
> other than user text (assistant output, a tool call or result, reasoning),
> the request carries no reply.

Replace the **Yes-grant scope** test-coverage sentence "one POSITIVE intent
covering subsequent held requests in the turn" with:

> one POSITIVE intent followed by at least three further over-threshold
> requests in the same session and turn yields exactly one replay and one
> upstream call; a re-sent reply does not release a second hold; a held
> request whose newest item is approval vocabulary cannot approve itself.

## Unchanged

`no` still discards. Pending TTL, the anti-loop window, the hard-block band
and rolling caps behave as before. TIP directive handling is unchanged.

## Follow-up outside this change

`[TIP: allow=once]` is read from the latest user turn even when tool items
follow it, so the same tool-loop persistence lets a single `allow=once`
authorize every held request in that turn. Applying the newest-item rule to
TIP authorization would close it. That change needs its own decision because
it alters documented directive behaviour.
