from __future__ import annotations

import json

from run_coach.review import build_context, validate_activity


def test_scenario_context(scenario: dict) -> None:
    activity = scenario["activity"]
    plan = scenario["plan"]
    profile = scenario["profile"]
    history = scenario["history"]

    validate_activity(activity)
    context = build_context(activity, plan, profile, history)

    completed = context["completed_activity"]
    assert completed["notes"] == activity["notes"]
    assert completed["activity_id"] == activity["activity_id"]
    assert context["planned_session"]["session_type"] == plan["session_type"]
    assert context["planned_session"]["purpose"] == plan["purpose"]
    assert context["athlete_profile"]["athlete_id"] == profile["athlete_id"]
    assert [row["activity_id"] for row in context["recent_history"]] == [
        item["activity_id"] for item in history
    ]
    packet = json.dumps(context)
    assert activity["notes"] in packet
    assert plan["prescription"] in packet
    for item in history:
        assert item["notes"] in packet
