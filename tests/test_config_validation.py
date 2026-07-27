from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ambientwill.config import ConfigError, load_policy
from ambientwill.models import AgentPolicy, ValidationError

BASE = """
[agent]
timezone = "UTC"
quiet_hours = []
daily_message_hard_limit = {daily}
unanswered_limit = {unanswered}
min_message_gap_minutes = {gap}
jitter_min_minutes = {jitter_min}
jitter_max_minutes = {jitter_max}
cooldown_minutes = {cooldown}
message_threshold = {message}
reflect_threshold = {reflect}
"""


def write_config(tmp_path: Path, **overrides: object) -> Path:
    values = {
        "daily": 10,
        "unanswered": 3,
        "gap": 60,
        "jitter_min": 5,
        "jitter_max": 35,
        "cooldown": 180,
        "message": 1.0,
        "reflect": 0.5,
    }
    values.update(overrides)
    path = tmp_path / "policy.toml"
    path.write_text(BASE.format(**values), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("daily", "0.9"),
        ("unanswered", "3.7"),
        ("jitter_min", "5.8"),
        ("jitter_max", "35.2"),
        ("daily", "true"),
    ],
)
def test_integer_fields_reject_float_and_bool(
    tmp_path: Path, field: str, value: str
) -> None:
    with pytest.raises(ConfigError):
        load_policy(write_config(tmp_path, **{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gap", "nan"),
        ("gap", "inf"),
        ("cooldown", "-1"),
        ("message", "nan"),
        ("message", "2.1"),
        ("reflect", "-1.1"),
    ],
)
def test_numeric_policy_values_are_finite_and_bounded(
    tmp_path: Path, field: str, value: str
) -> None:
    with pytest.raises(ConfigError):
        load_policy(write_config(tmp_path, **{field: value}))


def test_agent_policy_rejects_non_integer_limits(policy: AgentPolicy) -> None:
    with pytest.raises(ValidationError):
        replace(policy, daily_message_hard_limit=1.5)
