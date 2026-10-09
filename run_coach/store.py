from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


def supabase_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


def get_client(client: Any | None = None) -> Any:
    if client is not None:
        return client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")
    from supabase import create_client

    return create_client(url, key)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        loaded = json.loads(value)
        if not isinstance(loaded, dict):
            raise ValueError("Expected a JSON object.")
        return loaded
    if isinstance(value, dict):
        return value
    raise ValueError("Expected a JSON object.")


def activity_to_history_row(payload: dict[str, Any], started_at: str | None = None) -> dict[str, Any]:
    """Collapse a stored activity into the summary shape used as coach history."""
    started = payload.get("started_at") or started_at or ""
    date = started[:10] if isinstance(started, str) and len(started) >= 10 else None
    row = {
        "activity_id": payload.get("activity_id"),
        "date": date,
        "session_type": payload.get("name") or payload.get("sport"),
        "distance_mi": payload.get("distance_mi"),
        "duration_s": payload.get("duration_s"),
        "avg_pace_per_mile": payload.get("avg_pace_per_mile"),
        "avg_hr_bpm": payload.get("avg_hr_bpm"),
        "max_hr_bpm": payload.get("max_hr_bpm"),
        "elevation_gain_ft": payload.get("elevation_gain_ft"),
        "perceived_effort_1_10": payload.get("perceived_effort_1_10"),
        "notes": payload.get("notes"),
    }
    return {key: value for key, value in row.items() if value is not None}


def upsert_athlete(profile: dict[str, Any], *, client: Any | None = None) -> str:
    athlete_id = profile.get("athlete_id")
    if not athlete_id:
        raise ValueError("Athlete profile needs athlete_id to save to Supabase.")
    db = get_client(client)
    athlete_id = str(athlete_id)
    db.table("athletes").upsert(
        {
            "athlete_id": athlete_id,
            "profile": profile,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="athlete_id",
    ).execute()
    return athlete_id


def save_conversation_message(
    *,
    profile: dict[str, Any],
    role: str,
    content: str,
    created_at: str,
    client: Any | None = None,
) -> None:
    athlete_id = upsert_athlete(profile, client=client)
    db = get_client(client)
    db.table("conversation_messages").insert(
        {
            "athlete_id": athlete_id,
            "role": role,
            "content": content,
            "created_at": created_at,
        }
    ).execute()


def save_profile_change(
    *,
    profile: dict[str, Any],
    entry: dict[str, Any],
    client: Any | None = None,
) -> None:
    athlete_id = upsert_athlete(profile, client=client)
    db = get_client(client)
    db.table("profile_changes").insert(
        {
            "athlete_id": athlete_id,
            "field": entry["field"],
            "old_value": entry["old_value"],
            "new_value": entry["new_value"],
            "reason": entry["reason"],
            "status": entry["status"],
            "created_at": entry["created_at"],
        }
    ).execute()


def save_review_bundle(
    *,
    activity: dict[str, Any],
    plan: dict[str, Any],
    profile: dict[str, Any],
    review: dict[str, Any],
    model: str,
    email_body: str,
    client: Any | None = None,
) -> None:
    """Upsert the athlete, activity, and plan, then insert the review."""
    athlete_id = profile.get("athlete_id")
    activity_id = activity.get("activity_id")
    if not athlete_id:
        raise ValueError("Athlete profile needs athlete_id to save to Supabase.")
    if not activity_id:
        raise ValueError("Activity needs activity_id to save to Supabase.")

    athlete_id = upsert_athlete(profile, client=client)
    activity_id = str(activity_id)
    db = get_client(client)
    db.table("activities").upsert(
        {
            "activity_id": activity_id,
            "athlete_id": athlete_id,
            "started_at": activity.get("started_at"),
            "payload": activity,
        },
        on_conflict="activity_id",
    ).execute()
    db.table("planned_sessions").upsert(
        {
            "athlete_id": athlete_id,
            "activity_id": activity_id,
            "session_date": plan.get("date"),
            "payload": plan,
        },
        on_conflict="activity_id",
    ).execute()
    db.table("reviews").insert(
        {
            "activity_id": activity_id,
            "athlete_id": athlete_id,
            "model": model,
            "review": review,
            "email_body": email_body,
        }
    ).execute()


def persist_review_if_configured(
    *,
    activity: dict[str, Any],
    plan: dict[str, Any],
    profile: dict[str, Any],
    review: dict[str, Any],
    model: str,
    email_body: str,
    client: Any | None = None,
) -> bool:
    """Save when credentials or a client are present. Return whether a save ran."""
    if client is None and not supabase_configured():
        return False
    save_review_bundle(
        activity=activity,
        plan=plan,
        profile=profile,
        review=review,
        model=model,
        email_body=email_body,
        client=client,
    )
    return True


def load_recent_activities(
    athlete_id: str,
    *,
    limit: int = 12,
    exclude_activity_id: str | None = None,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Return stored activities oldest-first, in the coach history shape."""
    fetch_limit = limit + (1 if exclude_activity_id else 0)
    db = get_client(client)
    response = (
        db.table("activities")
        .select("activity_id, started_at, payload")
        .eq("athlete_id", athlete_id)
        .order("started_at", desc=True)
        .limit(fetch_limit)
        .execute()
    )
    rows = response.data or []
    selected: list[dict[str, Any]] = []
    for row in rows:
        if exclude_activity_id and str(row.get("activity_id")) == str(exclude_activity_id):
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    selected.reverse()
    history: list[dict[str, Any]] = []
    for row in selected:
        payload = _as_dict(row.get("payload") or {})
        if "activity_id" not in payload and row.get("activity_id"):
            payload["activity_id"] = row["activity_id"]
        started_at = row.get("started_at")
        history.append(activity_to_history_row(payload, started_at if isinstance(started_at, str) else None))
    return history
