from __future__ import annotations

import json
from pathlib import Path

import pytest

from run_coach.profile import (
    ProfileUpdateError,
    commit_profile_change,
    format_changelog,
    load_changelog,
    load_profile,
    propose_profile_update,
)


@pytest.fixture(autouse=True)
def no_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("run_coach.store.supabase_configured", lambda: False)


def _profile() -> dict:
    return {
        "athlete_id": "scenario-runner",
        "goals": ["City marathon"],
        "typical_week": {"running_days_per_week": 6, "usual_weekly_mileage_mi": 45},
        "known_limitations": ["None reported"],
        "fitness_estimates": {"max_hr_bpm": 188},
    }


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ProfileUpdateError, match="cannot be changed"):
        propose_profile_update(_profile(), "favorite_shoes", "vaporfly", "The athlete named these shoes.")


def test_diagnosis_cannot_be_stored_as_a_limitation() -> None:
    with pytest.raises(ProfileUpdateError, match="diagnosis"):
        propose_profile_update(
            _profile(),
            "known_limitations",
            ["diagnosed with a calf strain"],
            "The athlete said their calf hurt.",
        )


def test_running_days_must_be_in_range() -> None:
    with pytest.raises(ProfileUpdateError, match="0 to 7"):
        propose_profile_update(
            _profile(),
            "typical_week.running_days_per_week",
            9,
            "The athlete said they run every day plus doubles.",
        )


def test_confirmed_change_updates_profile_and_changelog(tmp_path: Path) -> None:
    profile = _profile()
    profile_path = tmp_path / "profile.json"
    changelog_path = tmp_path / "log.jsonl"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    proposal = propose_profile_update(
        profile,
        "typical_week.usual_weekly_mileage_mi",
        40,
        "The athlete said weekly mileage is now about 40.",
    )
    entry = commit_profile_change(
        profile,
        proposal,
        accepted=True,
        profile_path=profile_path,
        changelog_path=changelog_path,
    )

    saved = load_profile(profile_path)
    assert saved["typical_week"]["usual_weekly_mileage_mi"] == 40
    assert saved["goals"] == ["City marathon"]
    assert entry["status"] == "applied"
    assert entry["rules"] == ["allowed_field", "one_field", "athlete_confirmed"]
    assert load_changelog(changelog_path) == [entry]
    assert "40" in format_changelog(load_changelog(changelog_path))


def test_declined_change_is_logged_and_does_not_write_the_profile(tmp_path: Path) -> None:
    profile = _profile()
    profile_path = tmp_path / "profile.json"
    changelog_path = tmp_path / "log.jsonl"
    original = json.dumps(profile)
    profile_path.write_text(original, encoding="utf-8")
    proposal = propose_profile_update(
        profile,
        "goals",
        ["City marathon", "A fall 10k"],
        "The athlete added a fall 10k.",
    )
    entry = commit_profile_change(
        profile,
        proposal,
        accepted=False,
        profile_path=profile_path,
        changelog_path=changelog_path,
    )

    assert profile_path.read_text(encoding="utf-8") == original
    assert profile["goals"] == ["City marathon"]
    assert entry["status"] == "declined"
    assert load_changelog(changelog_path)[0]["rules"][-1] == "athlete_declined"
