from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_coach.review import activity_for_coach, format_pace_per_mile


class ActivityUpdateError(ValueError):
    """An activity lookup or edit broke a rule."""


ACTIVITY_FIELDS = (
    "elevation_gain_ft",
    "notes",
    "name",
    "perceived_effort_1_10",
    "splits",
)

DEVICE_BACKUP = {
    "elevation_gain_ft": "device_elevation_gain_ft",
    "splits": "device_splits",
    "notes": "device_notes",
    "name": "device_name",
}


@dataclass(frozen=True)
class ActivityProposal:
    activity_id: str
    field: str
    old_value: Any
    new_value: Any
    reason: str


def find_activity(activities: list[dict[str, Any]], *, activity_id: str | None = None, on_date: str | None = None) -> dict[str, Any]:
    if not activity_id and not on_date:
        raise ActivityUpdateError("Provide activity_id or a date like 2026-10-10.")
    matches = []
    for activity in activities:
        if activity_id and str(activity.get("activity_id")) == str(activity_id):
            return activity
        started = str(activity.get("started_at") or "")
        if on_date and started[:10] == on_date:
            matches.append(activity)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(str(item.get("activity_id")) for item in matches)
        raise ActivityUpdateError(f"More than one activity on {on_date}: {ids}.")
    label = activity_id or on_date
    raise ActivityUpdateError(f"No saved activity matches {label}.")


def activity_snapshot(activity: dict[str, Any]) -> dict[str, Any]:
    """Full coach view, including laps and heart rate."""
    return activity_for_coach(activity)


def _clean_splits(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ActivityUpdateError("splits must be a non-empty list of laps.")
    cleaned: list[dict[str, Any]] = []
    for index, split in enumerate(value, start=1):
        if not isinstance(split, dict):
            raise ActivityUpdateError("Each split must be an object.")
        distance = split.get("distance_mi")
        if isinstance(distance, bool) or not isinstance(distance, (int, float)) or distance <= 0:
            raise ActivityUpdateError("Each split needs a positive distance_mi.")
        duration = split.get("duration_s")
        pace = split.get("pace_per_mile")
        if duration is None and not pace:
            raise ActivityUpdateError("Each split needs duration_s or pace_per_mile.")
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0):
            raise ActivityUpdateError("duration_s must be a positive number of seconds.")
        row: dict[str, Any] = {
            "split": index,
            "distance_mi": round(float(distance), 2),
        }
        if duration is not None:
            row["duration_s"] = int(round(float(duration)))
            row["pace_per_mile"] = pace or format_pace_per_mile(float(duration), float(distance))
        else:
            row["pace_per_mile"] = str(pace)
        if split.get("start_mi") is not None:
            row["start_mi"] = split["start_mi"]
            row["end_mi"] = split.get("end_mi")
        heart_rate = split.get("avg_hr_bpm")
        if heart_rate is not None:
            number = _whole_number(heart_rate)
            if number is None:
                raise ActivityUpdateError("avg_hr_bpm must be a whole number.")
            row["avg_hr_bpm"] = number
        cleaned.append(row)
    return cleaned


def _whole_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _clean_value(field: str, value: Any) -> Any:
    if field == "elevation_gain_ft":
        number = _whole_number(value)
        if number is None or number < 0 or number > 20000:
            raise ActivityUpdateError("elevation_gain_ft must be a whole number of feet from 0 to 20000.")
        return number
    if field == "perceived_effort_1_10":
        if value is None:
            return None
        number = _whole_number(value)
        if number is None or number < 1 or number > 10:
            raise ActivityUpdateError("perceived_effort_1_10 must be a whole number from 1 to 10, or null.")
        return number
    if field in {"notes", "name"}:
        if not isinstance(value, str) or not value.strip():
            raise ActivityUpdateError(f"{field} must be a non-empty string.")
        return value.strip()
    if field == "splits":
        return _clean_splits(value)
    raise ActivityUpdateError(f"{field} cannot be edited. Allowed fields: {', '.join(ACTIVITY_FIELDS)}.")


def propose_activity_update(
    activity: dict[str, Any],
    field: str,
    new_value: Any,
    reason: str,
) -> ActivityProposal:
    if field not in ACTIVITY_FIELDS:
        raise ActivityUpdateError(f"{field} cannot be edited. Allowed fields: {', '.join(ACTIVITY_FIELDS)}.")
    if not isinstance(reason, str) or len(reason.strip()) < 8:
        raise ActivityUpdateError("A proposal needs a short reason based on what the athlete said.")
    cleaned = _clean_value(field, new_value)
    old_value = activity.get(field)
    if cleaned == old_value:
        raise ActivityUpdateError(f"{field} already has that value.")
    return ActivityProposal(
        activity_id=str(activity.get("activity_id")),
        field=field,
        old_value=old_value,
        new_value=cleaned,
        reason=reason.strip(),
    )


def format_activity_proposal(proposal: ActivityProposal) -> str:
    return "\n".join(
        [
            "Proposed activity change (not applied yet)",
            f"  activity: {proposal.activity_id}",
            f"  field: {proposal.field}",
            f"  from: {json.dumps(proposal.old_value, ensure_ascii=False)}",
            f"  to: {json.dumps(proposal.new_value, ensure_ascii=False)}",
            f"  reason: {proposal.reason}",
            "  rule: one field on this activity, saved only after you confirm",
        ]
    )


def _activity_path(directory: Path, activity_id: str) -> Path:
    direct = directory / f"{activity_id}.json"
    if direct.exists():
        return direct
    for path in directory.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("activity_id")) == str(activity_id):
            return path
    raise ActivityUpdateError(f"No activity file for {activity_id}.")


def commit_activity_change(
    activity: dict[str, Any],
    proposal: ActivityProposal,
    *,
    accepted: bool,
    activities_dir: Path,
) -> dict[str, Any]:
    """Apply or decline one correction and write it onto the activity file."""
    entry = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "field": proposal.field,
        "old_value": proposal.old_value,
        "new_value": proposal.new_value,
        "reason": proposal.reason,
        "status": "applied" if accepted else "declined",
    }
    if accepted:
        backup_key = DEVICE_BACKUP.get(proposal.field)
        if backup_key and backup_key not in activity:
            activity[backup_key] = proposal.old_value
        activity[proposal.field] = proposal.new_value
    corrections = list(activity.get("corrections") or [])
    corrections.append(entry)
    activity["corrections"] = corrections
    path = _activity_path(activities_dir, proposal.activity_id)
    path.write_text(json.dumps(activity, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry
