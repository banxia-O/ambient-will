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
