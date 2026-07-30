# Hermes wake/composition boundary

This directory records the minimal host boundary discovered during real-user
AmbientWill testing. It is **not** a runnable scheduler or delivery adapter.

```text
AmbientWill decides whether a wake is worthwhile
  -> passes private Desire / Urge / reason context
  -> Hermes reads the current conversation
  -> Hermes keeps its normal persona and decides SILENT or final prose
  -> a separate, deployment-specific adapter handles delivery
```

`message_preview` is private motivation, never final copy. The helper rejects an
exact preview echo and visible mechanism labels. If Hermes generation fails, it
returns silence rather than a fixed template.

Deployment-specific concerns—session/target binding, profile selection,
locking, delivery receipts, retries, channel authorization, and scheduling—are
intentionally outside this small module. They require their own reviewed host
adapter and must not be inferred from this reference boundary.

## Recurring Desire Progress

`progress_policy.py` is a pure, deterministic policy for one narrow recovery
case: an explicitly allowlisted recurring Desire has reached a terminal
delivery outcome (`sent`, `suppressed`, or `delivery_unknown`). It preserves the
current Desire projection, proposes an open Progress revision, and schedules a
future review. It does not retry or resend the original event.

The policy expects these private mappings:

- Desire: `id`, positive `revision`, `status`, `current_state`, `next_step`,
  `gap`, and `actionability`;
- receipt: `event_id`, `desire_id`, positive `desire_revision`, and `status`;
- reconciled receipts additionally carry `progress_revision`, which must equal
  `desire_revision + 1`.

`generating` and `sending` are nonterminal and produce no proposal. Desires
outside the explicit allowlist, missing Desires, and non-open Desires are not
rearmed. Invalid identities, revisions, timestamps, intervals, or status values
fail closed.

`progress_reconciliation.py` owns no storage. A deployment adapter injects:

```text
read_desire(desire_id)
append_progress(desire_id, event_id, proposal) -> new_revision
update_receipt(event_id, progress_revision)
```

`append_progress` must enforce `proposal.expected_revision` as an optimistic
compare-and-swap and translate a real CAS miss to `RevisionConflictError`.
After a successful append, the adapter stores the returned revision on the
receipt. If the process stops between those operations, a later call observes
exactly `desire_revision + 1` and repairs only the receipt marker. Any other
revision drift fails closed.

These helpers do not open files, invoke the CLI, call Hermes, schedule work, or
deliver messages. Production code still owns its receipt schema, transaction
boundaries, allowlist configuration, locking, and adapter-specific audit data.
