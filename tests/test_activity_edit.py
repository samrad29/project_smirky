from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from run_coach.activities import coach_context
from run_coach.activity_edit import commit_activity_change, find_activity, propose_activity_update
from run_coach.chat import complete_exchange
from run_coach.conversation import load_messages


def _older() -> dict:
    return {
        "activity_id": "long-run",
        "started_at": "2026-10-10T09:47:21-05:00",
        "sport": "running",
        "name": "Run",
        "distance_mi": 20.01,
        "duration_s": 12940,
        "avg_pace_per_mile": "10:47",
        "elevation_gain_ft": 2802,
        "avg_hr_bpm": 147,
        "max_hr_bpm": 188,
        "notes": "Imported from FIT. Device recorded 16 lap(s).",
        "splits": [
            {
                "split": 1,
                "start_mi": 0.0,
                "end_mi": 5.05,
                "distance_mi": 5.05,
                "duration_s": 3003,
                "pace_per_mile": "9:55",
                "avg_hr_bpm": 136,
            }
        ],
    }


def _newer() -> dict:
    return {
        "activity_id": "easy-after",
        "started_at": "2026-10-14T07:00:00-05:00",
        "sport": "running",
        "name": "Run",
        "distance_mi": 5.66,
        "duration_s": 3000,
        "avg_pace_per_mile": "8:50",
        "splits": [],
    }


def _save(directory: Path, activity: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{activity['activity_id']}.json").write_text(json.dumps(activity), encoding="utf-8")


def test_older_activity_keeps_a_split_count_in_the_summary() -> None:
    context = coach_context({}, [_newer(), _older()], [])
    earlier = context["earlier_activities"][0]
    assert earlier["activity_id"] == "long-run"
    assert earlier["split_count"] == 1
    assert "splits" not in earlier
    assert context["latest_activity"]["activity_id"] == "easy-after"


def test_confirmed_elevation_correction_keeps_the_device_value(tmp_path: Path) -> None:
    activity = _older()
    _save(tmp_path, activity)
    proposal = propose_activity_update(
        activity,
        "elevation_gain_ft",
        580,
        "The athlete said the climb was about 580 feet, not the device total.",
    )
    entry = commit_activity_change(activity, proposal, accepted=True, activities_dir=tmp_path)
    saved = json.loads((tmp_path / "long-run.json").read_text(encoding="utf-8"))
    assert entry["status"] == "applied"
    assert saved["elevation_gain_ft"] == 580
    assert saved["device_elevation_gain_ft"] == 2802
    assert saved["splits"][0]["avg_hr_bpm"] == 136
    assert saved["corrections"][0]["field"] == "elevation_gain_ft"


def test_declined_correction_does_not_change_the_activity(tmp_path: Path) -> None:
    activity = _older()
    _save(tmp_path, activity)
    proposal = propose_activity_update(
        activity,
        "notes",
        "Athlete drank 1 L and took 6 gels.",
        "The athlete reported fluids and gels for this long run.",
    )
    commit_activity_change(activity, proposal, accepted=False, activities_dir=tmp_path)
    saved = json.loads((tmp_path / "long-run.json").read_text(encoding="utf-8"))
    assert saved["notes"].startswith("Imported from FIT")
    assert saved["corrections"][0]["status"] == "declined"
    assert "device_notes" not in saved


def test_chat_can_load_splits_for_an_older_activity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("run_coach.store.supabase_configured", lambda: False)
    activities_dir = tmp_path / "activities"
    _save(activities_dir, _older())
    _save(activities_dir, _newer())
    activities = [_newer(), _older()]
    call = SimpleNamespace(
        type="function_call",
        name="get_activity",
        call_id="call_laps",
        arguments=json.dumps({"date": "2026-10-10"}),
    )
    client = SimpleNamespace(
        responses=_Script([
            SimpleNamespace(output=[call], output_text=""),
            SimpleNamespace(output=[], output_text="The first lap was 5.05 miles at 9:55 with heart rate 136."),
        ])
    )
    reply = complete_exchange(
        client=client,
        model="test-model",
        profile={"athlete_id": "local-runner"},
        activities=activities,
        pending=[],
        messages=[],
        user_message="What were the splits on the October 10 long run?",
        ask_confirm=lambda _prompt: False,
        profile_path=tmp_path / "profile.json",
        changelog_path=tmp_path / "log.jsonl",
        conversation_file=tmp_path / "chat.jsonl",
        activities_dir=activities_dir,
    )
    tool_output = client.responses.calls[1]["input"][-1]["output"]
    loaded = json.loads(tool_output)
    assert loaded["activity_id"] == "long-run"
    assert loaded["splits"][0]["avg_hr_bpm"] == 136
    assert loaded["splits"][0]["pace_per_mile"] == "9:55"
    assert "136" in reply
    assert "get_activity" in client.responses.calls[0]["instructions"]
    assert [message["role"] for message in load_messages(tmp_path / "chat.jsonl")] == ["user", "assistant"]


def test_chat_applies_a_confirmed_activity_edit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("run_coach.store.supabase_configured", lambda: False)
    activities_dir = tmp_path / "activities"
    older = _older()
    _save(activities_dir, older)
    activities = [_newer(), older]
    call = SimpleNamespace(
        type="function_call",
        name="propose_activity_update",
        call_id="call_edit",
        arguments=json.dumps(
            {
                "activity_id": "long-run",
                "field": "elevation_gain_ft",
                "new_value": 580,
                "reason": "The athlete said this run climbed about 580 feet.",
            }
        ),
    )
    client = SimpleNamespace(
        responses=_Script([
            SimpleNamespace(output=[call], output_text=""),
            SimpleNamespace(output=[], output_text="I saved 580 feet for that long run."),
        ])
    )
    prompts: list[str] = []
    complete_exchange(
        client=client,
        model="test-model",
        profile={"athlete_id": "local-runner"},
        activities=activities,
        pending=[],
        messages=[],
        user_message="The elevation was about 580 feet, not 2802.",
        ask_confirm=lambda prompt: prompts.append(prompt) or True,
        profile_path=tmp_path / "profile.json",
        changelog_path=tmp_path / "log.jsonl",
        conversation_file=tmp_path / "chat.jsonl",
        activities_dir=activities_dir,
    )
    assert prompts == ["Apply this activity change? [y/n] "]
    saved = json.loads((activities_dir / "long-run.json").read_text(encoding="utf-8"))
    assert saved["elevation_gain_ft"] == 580
    assert find_activity(activities, on_date="2026-10-10")["elevation_gain_ft"] == 580


class _Script:
    def __init__(self, script: list[SimpleNamespace]) -> None:
        self.script = script
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)
