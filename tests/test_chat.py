from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from run_coach.chat import complete_exchange, greeting, local_command, session_status
from run_coach.conversation import load_messages


@pytest.fixture(autouse=True)
def no_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("run_coach.store.supabase_configured", lambda: False)


def _profile() -> dict:
    return {"athlete_id": "scenario-runner", "goals": ["City marathon"], "typical_week": {"usual_weekly_mileage_mi": 45}}


def _activity() -> dict:
    return {
        "activity_id": "run-1",
        "started_at": "2026-06-12T17:40:00-05:00",
        "sport": "run",
        "name": "Easy run",
        "distance_mi": 6,
        "duration_s": 3300,
        "avg_pace_per_mile": "9:10",
        "notes": "Legs felt heavy.",
    }


class ScriptedResponses:
    def __init__(self, script: list[SimpleNamespace]) -> None:
        self.script = script
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


def _response(output: list, text: str) -> SimpleNamespace:
    return SimpleNamespace(output=output, output_text=text)


def _call(arguments: dict, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        name="propose_profile_update",
        call_id=call_id,
        arguments=json.dumps(arguments),
    )


def test_greeting_and_session_status(tmp_path: Path) -> None:
    profile = {"athlete_id": "scenario-runner", "name": "Sam"}
    pending = [tmp_path / "afternoon.fit"]
    status = session_status(profile, [_activity()], pending, prior_count=2)
    assert greeting(profile) == "Hi Sam. I'm your running coach."
    assert status.startswith("Hi Sam. I'm your running coach.")
    assert "afternoon.fit" in status
    assert "Easy run" in status
    assert "Continuing a saved conversation (2 messages)." in status


def test_local_commands_do_not_call_the_model() -> None:
    profile = _profile()
    activity = _activity()
    assert local_command("/profile", profile=profile, activities=[activity], pending=[], changelog=[])
    assert json.loads(
        local_command("/profile", profile=profile, activities=[activity], pending=[], changelog=[])
    )["athlete_id"] == "scenario-runner"
    assert local_command("/quit", profile=profile, activities=[], pending=[], changelog=[]) == "quit"
    assert local_command("/import", profile=profile, activities=[], pending=[], changelog=[]) == "import"
    assert local_command("hello", profile=profile, activities=[], pending=[], changelog=[]) is None


def test_opening_turn_is_saved_without_a_fake_user_message(tmp_path: Path) -> None:
    profile = _profile()
    conversation = tmp_path / "conversation.jsonl"
    messages: list[dict] = []
    client = SimpleNamespace(
        responses=ScriptedResponses([_response([], "How did the easy run feel in the last two miles?")])
    )
    reply = complete_exchange(
        client=client,
        model="test-model",
        profile=profile,
        activities=[_activity()],
        pending=[],
        messages=messages,
        user_message=None,
        ask_confirm=lambda _prompt: False,
        profile_path=tmp_path / "profile.json",
        changelog_path=tmp_path / "log.jsonl",
        conversation_file=conversation,
    )

    assert "easy run" in reply
    stored = load_messages(conversation)
    assert [message["role"] for message in stored] == ["assistant"]
    assert "profile update" not in stored[0]["content"].lower()
    assert client.responses.calls[0]["store"] is False


def test_confirmed_profile_update_is_the_only_way_the_profile_changes(tmp_path: Path) -> None:
    profile = _profile()
    profile_path = tmp_path / "profile.json"
    changelog_path = tmp_path / "log.jsonl"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    conversation = tmp_path / "conversation.jsonl"
    arguments = {
        "field": "typical_week.usual_weekly_mileage_mi",
        "new_value": 40,
        "reason": "The athlete said weekly mileage is about 40 right now.",
    }
    client = SimpleNamespace(
        responses=ScriptedResponses(
            [
                _response([_call(arguments)], ""),
                _response([], "I'll keep weekly mileage at 40."),
            ]
        )
    )
    prompts: list[str] = []

    reply = complete_exchange(
        client=client,
        model="test-model",
        profile=profile,
        activities=[_activity()],
        pending=[],
        messages=[],
        user_message="My weeks are closer to 40 miles now.",
        ask_confirm=lambda prompt: prompts.append(prompt) or True,
        profile_path=profile_path,
        changelog_path=changelog_path,
        conversation_file=conversation,
    )

    assert prompts == ["Apply this profile change? [y/n] "]
    assert json.loads(profile_path.read_text(encoding="utf-8"))["typical_week"]["usual_weekly_mileage_mi"] == 40
    assert "40" in reply
    tool_output = client.responses.calls[1]["input"][-1]
    assert tool_output["type"] == "function_call_output"
    assert "confirmed" in tool_output["output"]


def test_declined_and_invalid_proposals_do_not_change_the_profile(tmp_path: Path) -> None:
    profile = _profile()
    profile_path = tmp_path / "profile.json"
    changelog_path = tmp_path / "log.jsonl"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    original = profile_path.read_text(encoding="utf-8")
    declined = {
        "field": "goals",
        "new_value": ["City marathon", "Fall 10k"],
        "reason": "The athlete mentioned adding a fall 10k.",
    }
    invalid = {
        "field": "favorite_shoes",
        "new_value": "vaporfly",
        "reason": "The athlete named these shoes.",
    }
    client = SimpleNamespace(
        responses=ScriptedResponses(
            [
                _response([_call(declined, "call_decline")], ""),
                _response([], "Leaving the goals as they are."),
                _response([_call(invalid, "call_invalid")], ""),
                _response([], "I can't store that field."),
            ]
        )
    )

    complete_exchange(
        client=client,
        model="test-model",
        profile=profile,
        activities=[],
        pending=[],
        messages=[],
        user_message="Maybe a fall 10k later.",
        ask_confirm=lambda _prompt: False,
        profile_path=profile_path,
        changelog_path=changelog_path,
        conversation_file=tmp_path / "one.jsonl",
    )
    complete_exchange(
        client=client,
        model="test-model",
        profile=profile,
        activities=[],
        pending=[],
        messages=[],
        user_message="I like the vaporfly.",
        ask_confirm=lambda _prompt: (_ for _ in ()).throw(AssertionError("invalid proposals are not confirmed")),
        profile_path=profile_path,
        changelog_path=changelog_path,
        conversation_file=tmp_path / "two.jsonl",
    )

    assert profile_path.read_text(encoding="utf-8") == original
    log = changelog_path.read_text(encoding="utf-8")
    assert "declined" in log
    assert "favorite_shoes" not in log
