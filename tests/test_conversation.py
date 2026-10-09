from __future__ import annotations

import json
from pathlib import Path

import pytest

from run_coach.activities import format_inbox, format_workouts, load_activities, pending_fit_files
from run_coach.conversation import append_message, load_messages


@pytest.fixture(autouse=True)
def no_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("run_coach.store.supabase_configured", lambda: False)


def test_pending_fit_files_ignore_the_archive(tmp_path: Path) -> None:
    inbox = tmp_path / "FIT_files"
    archive = inbox / "old_files"
    archive.mkdir(parents=True)
    (inbox / "today.fit").write_bytes(b"new")
    (archive / "yesterday.fit").write_bytes(b"old")
    (inbox / "notes.txt").write_text("skip", encoding="utf-8")

    pending = pending_fit_files(inbox)

    assert [path.name for path in pending] == ["today.fit"]
    assert "today.fit" in format_inbox(pending)


def test_latest_activities_are_newest_first(tmp_path: Path) -> None:
    older = {
        "activity_id": "older",
        "started_at": "2026-06-01T08:00:00-05:00",
        "sport": "run",
        "distance_mi": 5,
        "avg_pace_per_mile": "9:30",
    }
    newer = {
        "activity_id": "newer",
        "started_at": "2026-06-03T08:00:00-05:00",
        "name": "Tempo",
        "distance_mi": 7,
        "avg_pace_per_mile": "7:40",
    }
    (tmp_path / "older.json").write_text(json.dumps(older), encoding="utf-8")
    (tmp_path / "newer.json").write_text(json.dumps(newer), encoding="utf-8")

    activities = load_activities(tmp_path)

    assert [item["activity_id"] for item in activities] == ["newer", "older"]
    assert "2026-06-03 Tempo, 7 mi at 7:40/mi" in format_workouts(activities)


def test_conversation_reloads_in_order(tmp_path: Path) -> None:
    path = tmp_path / "local-runner.jsonl"
    profile = {"athlete_id": "local-runner"}
    append_message(path, role="assistant", content="Hi.", profile=profile)
    append_message(path, role="user", content="The run felt flat.", profile=profile)

    messages = load_messages(path)

    assert [message["role"] for message in messages] == ["assistant", "user"]
    assert messages[1]["content"] == "The run felt flat."
