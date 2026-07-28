from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import make_urge

from ambientwill.cli import main
from ambientwill.engine import Engine

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_feedback_resets_only_prior_elapsed_plans(store, policy) -> None:
    store.add_urge(make_urge(urge_id="first"))
    Engine(policy, store).tick(at=NOW)
    assert store.unanswered_count(NOW) == 1

    store.record_feedback(NOW)
    assert store.unanswered_count(NOW) == 0

    store.add_urge(make_urge(urge_id="second", created_at=NOW + timedelta(minutes=1)))
    Engine(policy, store).tick(at=NOW + timedelta(hours=1))
    assert store.unanswered_count(NOW + timedelta(hours=1)) == 1


def test_feedback_is_monotonic(store) -> None:
    store.record_feedback(NOW)
    store.record_feedback(NOW)

    try:
        store.record_feedback(NOW - timedelta(seconds=1))
    except ValueError as exc:
        assert "earlier" in str(exc)
    else:
        raise AssertionError("backdated feedback must be rejected")


def test_feedback_cli_requires_aware_time(tmp_path: Path, capsys) -> None:
    config = tmp_path / "ambientwill.toml"
    data = tmp_path / "data"
    assert (
        main(["init", "--config", str(config), "--data-dir", str(data), "--json"]) == 0
    )
    capsys.readouterr()

    code = main(
        [
            "feedback-record",
            "--data-dir",
            str(data),
            "--at",
            "2026-01-01T12:00:00",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code != 0
    assert payload["decision"] == "SLEEP"
    assert payload["blocked_by"] == "input_error"


def test_status_reports_last_feedback(tmp_path: Path, capsys) -> None:
    config = tmp_path / "ambientwill.toml"
    data = tmp_path / "data"
    main(["init", "--config", str(config), "--data-dir", str(data), "--json"])
    capsys.readouterr()

    aware = datetime.now(UTC) - timedelta(seconds=1)
    assert (
        main(
            [
                "feedback-record",
                "--data-dir",
                str(data),
                "--at",
                aware.isoformat(),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["status", "--data-dir", str(data), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["last_feedback_at"] == aware.isoformat()
