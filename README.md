# AmbientWill

AmbientWill is a local-first proactive cognition and messaging layer for
persistent AI agents.

v0.1 is an **offline shadow simulator**. It evaluates whether a candidate
wake-up should `SLEEP`, `REFLECT`, or produce `MESSAGE_PLANNED`. A planned
message is only a local record of what a future real mode might do.

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
entire v0.1 installation without changing the host.

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

Every command that returns structured data supports `--json`.

## Decision loop

Each normal Tick:

1. reads the local policy and ledger;
2. applies pause, quiet-hours, daily-limit, unanswered-limit, and minimum-gap
   gates in a fixed order;
3. selects the highest scoring valid Urge;
4. applies the Urge cooldown;
5. calculates `urgency + confidence - interruption_cost`;
6. records one WakeEvent and, for `MESSAGE_PLANNED`, one atomic shadow Outbox
   event.

Time windows use the configured IANA timezone and left-closed, right-open
semantics. Cross-midnight windows are supported.

## Development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[test]" build
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src
.venv/bin/python -m build
```

## Current limitations

v0.1 does not generate message prose, deliver messages, query recent sessions,
bridge replies into a live conversation, classify feedback, learn thresholds,
run scheduled jobs, or integrate with Hermes. Those capabilities require a
separate adapter and safety review after shadow replay has been evaluated.

The SQLite schema is internal in v0.1 and may change before a host adapter is
introduced.

See [the Chinese PRD](docs/PRD.zh-CN.md) for the broader product direction.
