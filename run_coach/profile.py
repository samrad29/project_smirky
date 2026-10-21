from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProfileUpdateError(ValueError):
    """A profile proposal broke an update rule."""


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DIAGNOSIS_PHRASES = (
    "diagnosed with",
    "diagnosis is",
    "you have a strain",
    "you have a tear",
    "likely a strain",
    "likely a tear",
    "stress fracture",
)

FIELD_RULES: dict[str, dict[str, Any]] = {
    "name": {"type": "string", "max_length": 80},
    "goals": {"type": "string_list", "max_items": 8},
    "experience_summary": {"type": "string", "max_length": 1000},
    "typical_week.running_days_per_week": {"type": "int", "min": 0, "max": 7},
    "typical_week.usual_weekly_mileage_mi": {"type": "number", "min": 0, "max": 200},
    "typical_week.long_run_day": {"type": "string", "enum": WEEKDAYS},
    "typical_week.strength_training": {"type": "string", "max_length": 200},
    "training_preferences": {"type": "string_list", "max_items": 8},
    "constraints": {"type": "string_list", "max_items": 8},
    "known_limitations": {"type": "string_list", "max_items": 8, "forbid_diagnosis": True},
    "fitness_estimates.threshold_pace": {"type": "string", "max_length": 40},
    "fitness_estimates.threshold_hr_bpm": {"type": "int_or_null", "min": 40, "max": 230},
    "fitness_estimates.max_hr_bpm": {"type": "int_or_null", "min": 40, "max": 230},
    "fitness_estimates.notes": {"type": "string", "max_length": 500},
    "coaching_preferences.tone": {"type": "string", "max_length": 200},
    "coaching_preferences.ambition": {"type": "string", "max_length": 200},
    "coaching_preferences.ask_questions_when_uncertain": {"type": "bool"},
}

PROFILE_UPDATE_RULES = """
Profile update rules:
- Propose at most one allowed field, using propose_profile_update.
- The tool does not save the profile. The athlete confirms or declines in the terminal.
- Propose a change only from a fact the athlete stated. Do not invent goals, mileage, pace, or heart rate.
- known_limitations may store the athlete's own words about an ongoing limit or pain. Do not write a diagnosis.
- Do not store one-run facts there. Elevation, splits, fueling, and how a single workout felt belong on that activity.
- This conversation cannot change the training plan.
""".strip()


@dataclass(frozen=True)
class ProfileProposal:
    field: str
    old_value: Any
    new_value: Any
    reason: str


def load_profile(path: str | Path) -> dict[str, Any]:
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise ValueError("Athlete profile must be a JSON object.")
    if not profile.get("athlete_id"):
        raise ValueError("Athlete profile needs athlete_id.")
    return profile


def save_profile(path: str | Path, profile: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_field(profile: dict[str, Any], field: str) -> Any:
    current: Any = profile
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def set_field(profile: dict[str, Any], field: str, value: Any) -> None:
    parts = field.split(".")
    current = profile
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _require_int(value: Any, *, min_value: int, max_value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileUpdateError("Expected a whole number.")
    if value < min_value or value > max_value:
        raise ProfileUpdateError(f"Expected a number from {min_value} to {max_value}.")
    return value


def _validate_value(field: str, value: Any, rule: dict[str, Any]) -> Any:
    kind = rule["type"]
    if kind == "string":
        if not isinstance(value, str) or not value.strip():
            raise ProfileUpdateError(f"{field} must be a non-empty string.")
        cleaned = value.strip()
        if len(cleaned) > rule["max_length"]:
            raise ProfileUpdateError(f"{field} is longer than {rule['max_length']} characters.")
        allowed = rule.get("enum")
        if allowed and cleaned not in allowed:
            raise ProfileUpdateError(f"{field} must be one of: {', '.join(allowed)}.")
        return cleaned
    if kind == "string_list":
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
            raise ProfileUpdateError(f"{field} must be a list of non-empty strings.")
        if len(value) > rule["max_items"]:
            raise ProfileUpdateError(f"{field} can hold at most {rule['max_items']} items.")
        cleaned = [item.strip() for item in value]
        if rule.get("forbid_diagnosis"):
            blob = " ".join(cleaned).lower()
            for phrase in DIAGNOSIS_PHRASES:
                if phrase in blob:
                    raise ProfileUpdateError(
                        f"{field} cannot store a diagnosis ({phrase}). Record the athlete's own description instead."
                    )
        return cleaned
    if kind == "int":
        return _require_int(value, min_value=rule["min"], max_value=rule["max"])
    if kind == "int_or_null":
        if value is None:
            return None
        return _require_int(value, min_value=rule["min"], max_value=rule["max"])
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProfileUpdateError(f"{field} must be a number.")
        if value < rule["min"] or value > rule["max"]:
            raise ProfileUpdateError(f"{field} must be from {rule['min']} to {rule['max']}.")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise ProfileUpdateError(f"{field} must be true or false.")
        return value
    raise ProfileUpdateError(f"No validator for {field}.")


def propose_profile_update(profile: dict[str, Any], field: str, new_value: Any, reason: str) -> ProfileProposal:
    """Check one proposed field change. This does not write the profile."""
    if field not in FIELD_RULES:
        allowed = ", ".join(sorted(FIELD_RULES))
        raise ProfileUpdateError(f"{field} cannot be changed. Allowed fields: {allowed}.")
    if not isinstance(reason, str) or len(reason.strip()) < 8:
        raise ProfileUpdateError("A proposal needs a short reason based on what the athlete said.")
    cleaned = _validate_value(field, new_value, FIELD_RULES[field])
    old_value = get_field(profile, field)
    if cleaned == old_value:
        raise ProfileUpdateError(f"{field} already has that value.")
    return ProfileProposal(field=field, old_value=old_value, new_value=cleaned, reason=reason.strip())


def load_changelog(path: str | Path) -> list[dict[str, Any]]:
    file = Path(path)
    if not file.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


def _append_changelog(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def commit_profile_change(
    profile: dict[str, Any],
    proposal: ProfileProposal,
    *,
    accepted: bool,
    profile_path: str | Path,
    changelog_path: str | Path,
    client: Any | None = None,
) -> dict[str, Any]:
    """Apply or decline a confirmed decision and append the change log."""
    entry = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "athlete_id": profile.get("athlete_id"),
        "field": proposal.field,
        "old_value": proposal.old_value,
        "new_value": proposal.new_value,
        "reason": proposal.reason,
        "status": "applied" if accepted else "declined",
        "rules": ["allowed_field", "one_field", "athlete_confirmed" if accepted else "athlete_declined"],
    }
    if accepted:
        set_field(profile, proposal.field, proposal.new_value)
        save_profile(profile_path, profile)
    _append_changelog(Path(changelog_path), entry)
    _mirror_change(profile, entry, client)
    return entry


def _mirror_change(profile: dict[str, Any], entry: dict[str, Any], client: Any | None) -> None:
    from run_coach.store import save_profile_change, supabase_configured

    if client is None and not supabase_configured():
        return
    save_profile_change(profile=profile, entry=entry, client=client)


def format_proposal(proposal: ProfileProposal) -> str:
    return "\n".join(
        [
            "Proposed profile change (not applied yet)",
            f"  field: {proposal.field}",
            f"  from: {json.dumps(proposal.old_value, ensure_ascii=False)}",
            f"  to: {json.dumps(proposal.new_value, ensure_ascii=False)}",
            f"  reason: {proposal.reason}",
            "  rule: one allowed field, saved only after you confirm",
        ]
    )


def format_changelog(entries: list[dict[str, Any]], *, limit: int = 20) -> str:
    if not entries:
        return "No profile changes yet."
    lines = ["Profile change log"]
    for entry in entries[-limit:]:
        lines.append(
            f"- {entry['created_at']} {entry['status']} {entry['field']}: "
            f"{json.dumps(entry['old_value'], ensure_ascii=False)} -> "
            f"{json.dumps(entry['new_value'], ensure_ascii=False)}"
        )
        lines.append(f"  reason: {entry['reason']}")
    return "\n".join(lines)
