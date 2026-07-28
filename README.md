# AmbientWill

AmbientWill is a local-first proactive cognition and messaging layer for
persistent AI agents.

v0.2 remains an **offline shadow simulator** and adds a persistent Desire
Ledger with an append-only Progress loop. Deterministic Desire reviews create
candidate Urges; the existing Engine still decides whether a Tick should
`SLEEP`, `REFLECT`, or produce `MESSAGE_PLANNED`. A planned message is only a
local record of what a future real mode might do.

It never sends a real message.

## Safety boundary

The shadow core:

- uses only an explicitly selected TOML file and its own SQLite data directory;
- uses deterministic gates and scoring, without an LLM;
- makes no network requests;
- does not read or modify an agent host, its sessions, memory, skills, scheduler,
  gateway, configuration, or source code;
- has no messaging adapter, deployment hook, or autonomous repair loop.

AmbientWill is a removable sidecar, not an agent framework or a replacement for
Hermes. Removing this repository and its selected data directory removes the
entire v0.2 installation without changing the host.

## Requirements

- Python 3.11 or newer
- no runtime dependencies outside the Python standard library

## Quick start

Create a local configuration and SQLite ledger:

```bash
ambientwill init --config ./ambientwill.toml --data-dir ./data
```

Add an anonymous example Urge:

```bash
ambientwill urge-add \
  --config ./ambientwill.toml \
  --data-dir ./data \
  --type follow_up \
  --reason "Review the pending anonymous example." \
  --urgency 0.7 \
  --confidence 0.8 \
  --interruption-cost 0.2
```

Create an explicit Desire with its first deterministic review time:

```bash
ambientwill desire-add \
  --config ./ambientwill.toml \
  --data-dir ./data \
  --id example-goal \
  --source project_goal \
  --urge-type follow_up \
  --reason "Advance an anonymous example goal." \
  --target-state "The next checkpoint is complete." \
  --current-state "The checkpoint is pending." \
  --next-step "Complete the next anonymous checkpoint." \
  --importance 0.8 --gap 0.7 --confidence 0.6 \
  --actionability 0.9 --interruption-cost 0.2 \
  --cooldown-key example-goal \
  --next-review-at "2026-02-01T13:00:00+00:00" \
  --json
```

Preview a due review without changing the database, WAL/SHM, or project lock:

```bash
ambientwill desire-review \
  --config ./ambientwill.toml \
  --data-dir ./data \
  --at "2026-02-01T13:00:00+00:00" \
  --dry-run \
  --json
```

Append Progress with optimistic concurrency control:

```bash
ambientwill desire-progress \
  --config ./ambientwill.toml \
  --data-dir ./data \
  --id example-goal \
  --expected-revision 1 \
  --current-state "The checkpoint is in progress." \
  --next-step "Finish the checkpoint." \
  --gap 0.5 --actionability 0.8 \
  --next-review-at "2026-02-01T15:00:00+00:00" \
  --status open \
  --json
```

Explain a candidate decision without writing a WakeEvent or Outbox event:

```bash
ambientwill tick \
  --config ./ambientwill.toml \
  --data-dir ./data \
  --dry-run \
  --json
```

Simulate an exact time without writing state:

```bash
ambientwill simulate \
  --config examples/ambientwill.example.toml \
  --data-dir ./tmp/sim \
  --at "2026-01-01T22:15:00+08:00" \
  --json
```

Inspect the latest committed decision:

```bash
ambientwill why --data-dir ./data --json
```

For an offline shadow replay, record a simulated user response locally:

```bash
ambientwill feedback-record \
  --data-dir ./data \
  --at "2026-01-01T12:00:00+08:00" \
  --json
```

This command only advances a local `last_feedback_at` marker. It is test input,
not a reply bridge, and it never reads a host conversation.

Every command that returns structured data supports `--json`.

## Decision loop

Each Desire review:

1. considers only open, due Desire revisions in stable order;
2. calculates `importance × gap + confidence × actionability - interruption_cost`;
3. records `SLEEP`, `EXPIRED`, or atomically creates one ordinary open Urge;
4. records at most one Review per `(desire_id, revision)`;
5. requires new append-only Progress to increment the revision and restore
   review eligibility.

The reviewer never calls the Engine. A normal Tick consumes the resulting Urge
through the unchanged v0.1 gates, budgets, jitter, WakeEvent, and shadow Outbox.

Each normal Tick:

1. reads the local policy and ledger;
2. applies pause, quiet-hours, unanswered-limit, and minimum-gap gates;
3. removes Urges whose cooldown key is still active, then selects the highest
   scoring eligible Urge;
4. calculates `urgency + confidence - interruption_cost`;
5. applies jitter, then re-checks quiet hours and the local-day message budget
   at `delayed_until`;
6. records one WakeEvent and, for `MESSAGE_PLANNED`, atomically records one
   shadow Outbox event and closes the selected Urge.

Time windows use the configured IANA timezone and left-closed, right-open
semantics. Cross-midnight windows are supported. Quiet-hour clocks must use
strict `HH:MM` format.

New data directories are created with mode `0700`; the SQLite database and
project lock use `0600`. Writable operations refuse database or directory
symlinks and refuse existing data directories that are accessible by group or
others. Read-only commands copy the SQLite/WAL state into an in-memory snapshot
before querying, so they do not modify the selected data directory.

## Development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[test]" build twine
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m build
.venv/bin/python -m twine check dist/*
```

## Current limitations

v0.2 does not create Desires automatically, increase pressure with elapsed
time, generate message prose, deliver messages, query recent sessions, bridge
replies into a live conversation, classify feedback, learn thresholds, run
scheduled jobs, or integrate with Hermes. It does not call an LLM or access the
network. Those capabilities require separate specifications and safety review.

The SQLite schema is internal in v0.2 and may change before a host adapter is
introduced.

See [the Chinese PRD](docs/PRD.zh-CN.md) for the broader product direction.
