from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from run_coach.fit_import import fit_files_in
from run_coach.review import activity_for_coach
from run_coach.store import activity_to_history_row


def pending_fit_files(inbox: str | Path) -> list[Path]:
    """FIT files still in the inbox. Archived files in old_files are ignored."""
    directory = Path(inbox)
    if not directory.is_dir():
        return []
    return fit_files_in(directory)


def load_activities(directory: str | Path) -> list[dict[str, Any]]:
    """Normalized activities, newest first."""
    folder = Path(directory)
    if not folder.is_dir():
        return []
    activities: list[dict[str, Any]] = []
    for path in folder.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("activity_id") and payload.get("started_at"):
            activities.append(payload)
    activities.sort(key=lambda item: str(item.get("started_at")), reverse=True)
    return activities


def format_activity_line(activity: dict[str, Any]) -> str:
    started = str(activity.get("started_at", ""))[:10]
    name = activity.get("name") or activity.get("sport") or "run"
    distance = activity.get("distance_mi")
    pace = activity.get("avg_pace_per_mile")
    distance_text = f"{distance} mi" if distance is not None else "distance unknown"
    pace_text = f" at {pace}/mi" if pace else ""
    return f"{started} {name}, {distance_text}{pace_text}"


def format_inbox(files: list[Path]) -> str:
    if not files:
        return "No new FIT files in data/FIT_files."
    names = ", ".join(path.name for path in files)
    return f"{len(files)} FIT file(s) not imported yet: {names}."


def format_workouts(activities: list[dict[str, Any]], *, limit: int = 5) -> str:
    if not activities:
        return "No imported workouts yet."
    lines = ["Latest workouts:"]
    for activity in activities[:limit]:
        lines.append(f"- {format_activity_line(activity)}")
    return "\n".join(lines)


def coach_context(profile: dict[str, Any], activities: list[dict[str, Any]], pending: list[Path]) -> dict[str, Any]:
    """Small fixed context: profile, inbox, newest activity, and a few prior summaries."""
    latest = None
    if activities:
        latest = activity_for_coach(activities[0])
        splits = latest.get("splits") or []
        if len(splits) > 20:
            latest["splits"] = splits[:20]
            latest["splits_truncated"] = True
    prior = []
    for activity in activities[1:5]:
        row = activity_to_history_row(activity)
        row["split_count"] = len(activity.get("splits") or [])
        prior.append(row)
    return {
        "athlete_profile": profile,
        "uningested_fit_files": [path.name for path in pending],
        "latest_activity": latest,
        "earlier_activities": prior,
    }
