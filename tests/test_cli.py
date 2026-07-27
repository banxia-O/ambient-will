import json
from pathlib import Path

from ambientwill.cli import main


def test_init_config_check_and_json_output(tmp_path: Path, capsys) -> None:
    config = tmp_path / "ambientwill.toml"
    data = tmp_path / "data"

    assert main(["init", "--config", str(config), "--data-dir", str(data), "--json"]) == 0
    init_payload = json.loads(capsys.readouterr().out)
    assert init_payload["ok"] is True

    assert main(["config-check", "--config", str(config), "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["ok"] is True


def test_urge_add_tick_dry_run_and_simulate_do_not_write(tmp_path: Path, capsys) -> None:
    config = tmp_path / "ambientwill.toml"
    data = tmp_path / "data"
    main(["init", "--config", str(config), "--data-dir", str(data), "--json"])
    capsys.readouterr()

    assert (
        main(
            [
                "urge-add",
                "--config",
                str(config),
                "--data-dir",
                str(data),
                "--type",
                "follow_up",
                "--reason",
                "Anonymous example",
                "--urgency",
                "0.7",
                "--confidence",
                "0.8",
                "--interruption-cost",
                "0.2",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["tick", "--config", str(config), "--data-dir", str(data), "--dry-run", "--json"]) == 0
    json.loads(capsys.readouterr().out)

    assert (
        main(
            [
                "simulate",
                "--config",
                str(config),
                "--data-dir",
                str(data),
                "--at",
                "2026-01-01T22:15:00+08:00",
                "--json",
            ]
        )
        == 0
    )
    json.loads(capsys.readouterr().out)

    assert main(["events", "--data-dir", str(data), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == []


def test_invalid_config_returns_nonzero_json(tmp_path: Path, capsys) -> None:
    config = tmp_path / "bad.toml"
    config.write_text("[agent]\ntimezone = 'Missing/Zone'\n", encoding="utf-8")

    assert main(["config-check", "--config", str(config), "--json"]) != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_corrupt_database_fails_closed(tmp_path: Path, capsys) -> None:
    config = tmp_path / "ambientwill.toml"
    data = tmp_path / "data"
    data.mkdir()
    (data / "ambientwill.db").write_text("not sqlite", encoding="utf-8")
    config.write_text(
        """
[agent]
timezone = "UTC"
quiet_hours = []
daily_message_hard_limit = 10
unanswered_limit = 3
min_message_gap_minutes = 60
jitter_min_minutes = 5
jitter_max_minutes = 35
cooldown_minutes = 180
message_threshold = 1.0
reflect_threshold = 0.5
""".strip(),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "tick",
                "--config",
                str(config),
                "--data-dir",
                str(data),
                "--json",
            ]
        )
        != 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert not (data / "ambientwill.db-wal").exists()
