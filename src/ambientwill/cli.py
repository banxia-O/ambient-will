from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from ambientwill import __version__
from ambientwill.config import ConfigError, load_policy, write_default_config
from ambientwill.engine import Engine
from ambientwill.models import Urge, ValidationError
from ambientwill.storage import AlreadyRunningError, Storage, TickLock

DEFAULT_CONFIG_PATH = Path("ambientwill.toml")
DEFAULT_DATA_DIR = Path("data")
DATABASE_NAME = "ambientwill.db"


class ArgumentParsingError(ValueError):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ArgumentParsingError(message)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="path to the TOML policy",
    )


def _add_data_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        help="AmbientWill-owned runtime directory",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        prog="ambientwill",
        description="AmbientWill v0.1 offline shadow simulator",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser(
        "init", help="write a local policy and initialize SQLite"
    )
    _add_config(init)
    _add_data_dir(init)
    _add_json(init)

    config_check = subparsers.add_parser("config-check", help="validate a TOML policy")
    _add_config(config_check)
    _add_json(config_check)

    urge_add = subparsers.add_parser("urge-add", help="add an anonymous local urge")
    _add_config(urge_add)
    _add_data_dir(urge_add)
    urge_add.add_argument("--id")
    urge_add.add_argument("--type", required=True)
    urge_add.add_argument("--reason", required=True)
    urge_add.add_argument("--urgency", required=True, type=float)
    urge_add.add_argument("--confidence", required=True, type=float)
    urge_add.add_argument("--interruption-cost", required=True, type=float)
    urge_add.add_argument("--cooldown-key")
    urge_add.add_argument("--expires-at")
    _add_json(urge_add)

    tick = subparsers.add_parser("tick", help="evaluate one candidate wake-up")
    _add_config(tick)
    _add_data_dir(tick)
    tick.add_argument("--dry-run", action="store_true")
    tick.add_argument("--at", help="aware ISO-8601 evaluation time")
    _add_json(tick)

    simulate = subparsers.add_parser("simulate", help="run a read-only evaluation")
    _add_config(simulate)
    _add_data_dir(simulate)
    simulate.add_argument("--at", required=True, help="aware ISO-8601 evaluation time")
    _add_json(simulate)

    status = subparsers.add_parser("status", help="show local shadow-core status")
    _add_data_dir(status)
    _add_json(status)

    pause = subparsers.add_parser("pause", help="pause evaluations until a time")
    _add_config(pause)
    _add_data_dir(pause)
    pause.add_argument("--until", required=True, help="ISO-8601 time")
    _add_json(pause)

    resume = subparsers.add_parser("resume", help="clear a local pause")
    _add_data_dir(resume)
    _add_json(resume)

    feedback = subparsers.add_parser(
        "feedback-record", help="record simulated local feedback for shadow replay"
    )
    _add_data_dir(feedback)
    feedback.add_argument("--at", required=True, help="aware ISO-8601 feedback time")
    _add_json(feedback)

    events = subparsers.add_parser("events", help="list recent WakeEvents")
    _add_data_dir(events)
    events.add_argument("--limit", type=int, default=50)
    _add_json(events)

    why = subparsers.add_parser("why", help="explain the most recent WakeEvent")
    _add_data_dir(why)
    _add_json(why)
    return parser


def _database_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / DATABASE_NAME


def _parse_datetime(
    value: str, default_zone, *, require_aware: bool = False
) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 datetime: {value}") from exc
    if parsed.tzinfo is None:
        if require_aware:
            raise ValueError("datetime must include a timezone")
        parsed = parsed.replace(tzinfo=default_zone)
    return parsed


def _writable_storage(data_dir: str | Path) -> Storage:
    path = _database_path(data_dir)
    if not path.exists():
        raise FileNotFoundError(f"database not found: {path}; run ambientwill init")
    return Storage(path)


def _emit(payload: dict[str, Any], as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(human)


def _run(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if args.command == "init":
        config_path = Path(args.config)
        storage = Storage(_database_path(args.data_dir))
        with TickLock(storage.lock_path):
            created_config = False
            if not config_path.exists():
                write_default_config(config_path)
                created_config = True
            policy = load_policy(config_path)
            storage._initialize_unlocked()
        payload = {
            "ok": True,
            "config": str(config_path),
            "data_dir": str(Path(args.data_dir)),
            "database": str(storage.path),
            "created_config": created_config,
            "policy": policy.to_dict(),
        }
        return payload, f"Initialized AmbientWill shadow state at {storage.path}"

    if args.command == "config-check":
        policy = load_policy(args.config)
        return {
            "ok": True,
            "config": str(Path(args.config)),
            "policy": policy.to_dict(),
        }, "Configuration is valid"

    if args.command == "urge-add":
        policy = load_policy(args.config)
        storage = _writable_storage(args.data_dir)
        created_at = datetime.now(policy.zone)
        expires_at = (
            _parse_datetime(args.expires_at, policy.zone) if args.expires_at else None
        )
        urge = Urge(
            id=args.id or f"urge_{uuid.uuid4().hex}",
            type=args.type,
            reason=args.reason,
            urgency=args.urgency,
            confidence=args.confidence,
            interruption_cost=args.interruption_cost,
            cooldown_key=args.cooldown_key or args.type,
            created_at=created_at,
            expires_at=expires_at,
            status="open",
        )
        storage.add_urge(urge)
        return {"ok": True, "urge": urge.to_dict()}, f"Added urge {urge.id}"

    if args.command in {"tick", "simulate"}:
        policy = load_policy(args.config)
        at = (
            _parse_datetime(args.at, policy.zone)
            if args.at
            else datetime.now(policy.zone)
        )
        dry_run = args.command == "simulate" or args.dry_run
        path = _database_path(args.data_dir)
        storage = (
            Storage.snapshot(path) if dry_run else _writable_storage(args.data_dir)
        )
        result = Engine(policy, storage).tick(
            at=at,
            trigger=args.command,
            dry_run=dry_run,
        )
        payload = {"ok": result.error is None, **result.to_dict()}
        return payload, (
            f"{result.decision.value}"
            + (f" (blocked by {result.blocked_by})" if result.blocked_by else "")
        )

    if args.command == "status":
        storage = Storage.snapshot(_database_path(args.data_dir))
        paused = storage.paused_until()
        feedback = storage.last_feedback_at()
        payload = {
            "ok": True,
            "paused_until": paused.isoformat() if paused else None,
            "last_feedback_at": feedback.isoformat() if feedback else None,
            "wake_events": storage.count_wake_events(),
            "outbox_events": storage.count_outbox(),
            "last_event": storage.last_wake_event(),
        }
        return payload, (
            f"wake_events={payload['wake_events']} "
            f"outbox_events={payload['outbox_events']}"
        )

    if args.command == "pause":
        policy = load_policy(args.config)
        until = _parse_datetime(args.until, policy.zone)
        storage = _writable_storage(args.data_dir)
        storage.pause(until)
        return {
            "ok": True,
            "paused_until": until.isoformat(),
        }, f"Paused until {until.isoformat()}"

    if args.command == "resume":
        storage = _writable_storage(args.data_dir)
        storage.resume()
        return {"ok": True, "paused_until": None}, "AmbientWill resumed"

    if args.command == "feedback-record":
        storage = _writable_storage(args.data_dir)
        at = _parse_datetime(args.at, None, require_aware=True)
        storage.record_feedback(at)
        return {
            "ok": True,
            "last_feedback_at": at.isoformat(),
        }, f"Recorded shadow feedback at {at.isoformat()}"

    if args.command == "events":
        if args.limit < 1:
            raise ValueError("--limit must be at least 1")
        storage = Storage.snapshot(_database_path(args.data_dir))
        events = storage.list_wake_events(limit=args.limit)
        return {"ok": True, "events": events}, f"{len(events)} event(s)"

    if args.command == "why":
        storage = Storage.snapshot(_database_path(args.data_dir))
        event = storage.last_wake_event()
        return {"ok": True, "event": event}, (
            "No WakeEvent has been recorded"
            if event is None
            else f"{event['decision']}: {len(event['reasons'])} gate result(s)"
        )

    raise ValueError(f"unknown command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = list(argv) if argv is not None else sys.argv[1:]
    json_requested = "--json" in arguments
    try:
        args = parser.parse_args(arguments)
    except ArgumentParsingError as exc:
        payload = {
            "ok": False,
            "decision": "SLEEP",
            "blocked_by": "argument_error",
            "error": "argument_error",
            "message": str(exc),
        }
        if json_requested:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            parser.print_usage(sys.stderr)
            print(f"ambientwill: error: {exc}", file=sys.stderr)
        return 2
    try:
        payload, human = _run(args)
        _emit(payload, args.json, human)
        return 0 if payload.get("ok", True) else 1
    except (
        ConfigError,
        ValidationError,
        ValueError,
        FileNotFoundError,
        OSError,
        sqlite3.Error,
        AlreadyRunningError,
    ) as exc:
        if isinstance(exc, ConfigError):
            blocked_by = "config_error"
        elif isinstance(exc, (FileNotFoundError, OSError, sqlite3.Error)):
            blocked_by = "storage_error"
        elif isinstance(exc, AlreadyRunningError):
            blocked_by = "already_running"
        else:
            blocked_by = "input_error"
        payload = {
            "ok": False,
            "decision": "SLEEP",
            "blocked_by": blocked_by,
            "error": exc.__class__.__name__,
            "message": str(exc),
        }
        _emit(payload, getattr(args, "json", False), f"Error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
