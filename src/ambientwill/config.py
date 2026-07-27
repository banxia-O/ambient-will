from __future__ import annotations

import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Any

from ambientwill.models import AgentPolicy, QuietWindow, ValidationError


class ConfigError(ValueError):
    """Raised when a TOML policy cannot be loaded safely."""


DEFAULT_CONFIG = """\
[agent]
timezone = "UTC"
quiet_hours = [
  { start = "23:00", end = "07:00" },
]
daily_message_hard_limit = 10
unanswered_limit = 3
min_message_gap_minutes = 60
jitter_min_minutes = 5
jitter_max_minutes = 35
cooldown_minutes = 180
message_threshold = 1.0
reflect_threshold = 0.5
"""


def _require_number(table: dict[str, Any], key: str) -> int | float:
    value = table.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"agent.{key} must be a number")
    return value


def load_policy(path: str | Path) -> AgentPolicy:
    config_path = Path(path)
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config {config_path}: {exc}") from exc

    agent = payload.get("agent")
    if not isinstance(agent, dict):
        raise ConfigError("missing [agent] table")

    timezone = agent.get("timezone")
    if not isinstance(timezone, str) or not timezone.strip():
        raise ConfigError("agent.timezone must be a non-empty IANA timezone")

    raw_windows = agent.get("quiet_hours", [])
    if not isinstance(raw_windows, list):
        raise ConfigError("agent.quiet_hours must be an array of tables")
    windows: list[QuietWindow] = []
    for index, item in enumerate(raw_windows):
        if not isinstance(item, dict):
            raise ConfigError(f"agent.quiet_hours[{index}] must be a table")
        start, end = item.get("start"), item.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ConfigError(
                f"agent.quiet_hours[{index}] requires string start and end"
            )
        try:
            windows.append(QuietWindow(start, end))
        except ValidationError as exc:
            raise ConfigError(str(exc)) from exc

    try:
        policy = AgentPolicy(
            timezone=timezone,
            quiet_hours=tuple(windows),
            daily_message_hard_limit=int(
                _require_number(agent, "daily_message_hard_limit")
            ),
            unanswered_limit=int(_require_number(agent, "unanswered_limit")),
            min_message_gap=timedelta(
                minutes=float(_require_number(agent, "min_message_gap_minutes"))
            ),
            jitter_min_minutes=int(
                _require_number(agent, "jitter_min_minutes")
            ),
            jitter_max_minutes=int(
                _require_number(agent, "jitter_max_minutes")
            ),
            cooldown=timedelta(
                minutes=float(_require_number(agent, "cooldown_minutes"))
            ),
            message_threshold=float(
                _require_number(agent, "message_threshold")
            ),
            reflect_threshold=float(
                _require_number(agent, "reflect_threshold")
            ),
        )
    except (ValidationError, OverflowError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc
    return policy


def write_default_config(path: str | Path) -> Path:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot write config {config_path}: {exc}") from exc
    return config_path
