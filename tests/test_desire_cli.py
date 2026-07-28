from __future__ import annotations

import json
from pathlib import Path

from ambientwill.cli import main


def initialize(tmp_path: Path, capsys) -> tuple[Path, Path]:
    config = tmp_path / "ambientwill.toml"
    data = tmp_path / "data"
    assert (
        main(["init", "--config", str(config), "--data-dir", str(data), "--json"]) == 0
    )
    capsys.readouterr()
    return config, data


def desire_add_args(config: Path, data: Path) -> list[str]:
    return [
        "desire-add",
        "--config",
        str(config),
        "--data-dir",
        str(data),
        "--id",
        "desire-cli",
        "--source",
        "user_directive",
        "--urge-type",
        "follow_up",
        "--reason",
        "Advance an anonymous project goal.",
        "--target-state",
        "The checkpoint is complete.",
        "--current-state",
        "The checkpoint is pending.",
        "--next-step",
        "Complete the checkpoint.",
        "--importance",
        "0.8",
        "--gap",
        "0.7",
        "--confidence",
        "0.6",
        "--actionability",
        "0.9",
        "--interruption-cost",
        "0.2",
        "--cooldown-key",
        "project-checkpoint",
        "--created-at",
        "2026-02-01T12:00:00+00:00",
        "--next-review-at",
        "2026-02-01T13:00:00+00:00",
        "--expires-at",
        "2026-02-08T12:00:00+00:00",
        "--json",
    ]


def test_desire_add_list_and_show_json(tmp_path: Path, capsys) -> None:
    config, data = initialize(tmp_path, capsys)

    assert main(desire_add_args(config, data)) == 0
    added = json.loads(capsys.readouterr().out)
    assert added["desire"]["id"] == "desire-cli"
    assert added["desire"]["revision"] == 1

    assert (
        main(
            [
                "desire-list",
                "--config",
                str(config),
                "--data-dir",
                str(data),
                "--status",
                "open",
                "--limit",
                "10",
                "--json",
            ]
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in listed["desires"]] == ["desire-cli"]

    assert (
        main(
            [
                "desire-show",
                "--config",
                str(config),
                "--data-dir",
                str(data),
                "--id",
                "desire-cli",
                "--json",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["desire"]["id"] == "desire-cli"
    assert shown["progress"] == []
    assert shown["reviews"] == []


def test_duplicate_desire_add_is_machine_readable_and_preserves_original(
    tmp_path: Path, capsys
) -> None:
    config, data = initialize(tmp_path, capsys)
    assert main(desire_add_args(config, data)) == 0
    capsys.readouterr()

    assert main(desire_add_args(config, data)) == 2
    duplicate = json.loads(capsys.readouterr().out)
    assert duplicate["ok"] is False
    assert duplicate["blocked_by"] == "storage_error"

    assert (
        main(
            [
                "desire-show",
                "--data-dir",
                str(data),
                "--id",
                "desire-cli",
                "--json",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["desire"]["reason"] == "Advance an anonymous project goal."


def test_desire_cli_rejects_naive_time_and_missing_id(tmp_path: Path, capsys) -> None:
    config, data = initialize(tmp_path, capsys)
    arguments = desire_add_args(config, data)
    arguments[arguments.index("2026-02-01T12:00:00+00:00")] = "2026-02-01T12:00:00"

    assert main(arguments) == 2
    invalid_time = json.loads(capsys.readouterr().out)
    assert invalid_time["blocked_by"] == "input_error"

    assert main(["desire-show", "--data-dir", str(data), "--json"]) == 2
    missing_id = json.loads(capsys.readouterr().out)
    assert missing_id["blocked_by"] in {"argument_error", "input_error"}


def progress_args(config: Path, data: Path, *, expected: str = "1") -> list[str]:
    return [
        "desire-progress",
        "--config",
        str(config),
        "--data-dir",
        str(data),
        "--id",
        "desire-cli",
        "--progress-id",
        "progress-cli",
        "--expected-revision",
        expected,
        "--recorded-at",
        "2026-02-01T12:30:00+00:00",
        "--current-state",
        "The checkpoint is in progress.",
        "--next-step",
        "Finish the checkpoint.",
        "--gap",
        "0.5",
        "--actionability",
        "0.8",
        "--next-review-at",
        "2026-02-01T14:00:00+00:00",
        "--status",
        "open",
        "--note",
        "Anonymous progress note.",
        "--json",
    ]


def test_desire_progress_cli_requires_expected_revision_and_appends_history(
    tmp_path: Path, capsys
) -> None:
    config, data = initialize(tmp_path, capsys)
    assert main(desire_add_args(config, data)) == 0
    capsys.readouterr()

    assert main(progress_args(config, data)) == 0
    progressed = json.loads(capsys.readouterr().out)
    assert progressed["desire"]["revision"] == 2
    assert progressed["progress"]["from_revision"] == 1
    assert progressed["progress"]["to_revision"] == 2

    assert main(progress_args(config, data)) == 2
    conflict = json.loads(capsys.readouterr().out)
    assert conflict["blocked_by"] == "input_error"
    assert "revision conflict" in conflict["message"]

    assert (
        main(
            [
                "desire-show",
                "--data-dir",
                str(data),
                "--id",
                "desire-cli",
                "--json",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["desire"]["revision"] == 2
    assert len(shown["progress"]) == 1


def review_args(config: Path, data: Path, *, dry_run: bool = False) -> list[str]:
    arguments = [
        "desire-review",
        "--config",
        str(config),
        "--data-dir",
        str(data),
        "--at",
        "2026-02-01T13:00:00+00:00",
        "--json",
    ]
    if dry_run:
        arguments.insert(-1, "--dry-run")
    return arguments


def test_desire_review_cli_dry_run_commit_and_replay(tmp_path: Path, capsys) -> None:
    config, data = initialize(tmp_path, capsys)
    assert main(desire_add_args(config, data)) == 0
    capsys.readouterr()

    assert main(review_args(config, data, dry_run=True)) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert preview["results"][0]["outcome"] == "URGE_CREATED"
    assert preview["results"][0]["would_create_urge"] is True

    assert main(review_args(config, data)) == 0
    committed = json.loads(capsys.readouterr().out)
    assert committed["dry_run"] is False
    assert committed["results"][0]["outcome"] == "URGE_CREATED"

    assert main(review_args(config, data)) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["results"][0]["outcome"] == "already_reviewed"

    assert (
        main(
            [
                "desire-show",
                "--data-dir",
                str(data),
                "--id",
                "desire-cli",
                "--json",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert len(shown["reviews"]) == 1


def test_desire_review_cli_requires_aware_at(tmp_path: Path, capsys) -> None:
    config, data = initialize(tmp_path, capsys)

    assert (
        main(
            [
                "desire-review",
                "--config",
                str(config),
                "--data-dir",
                str(data),
                "--at",
                "2026-02-01T13:00:00",
                "--json",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["blocked_by"] == "input_error"
