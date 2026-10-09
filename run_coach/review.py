from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field


class ReviewOutput(BaseModel):
    subject: str = Field(description="Short email subject line.")
    summary: str = Field(description="One- or two-sentence overall assessment.")
    what_went_well: list[str]
    deviations_from_plan: list[str]
    interpretation: str = Field(description="Evidence-based interpretation in athlete-specific context.")
    uncertainty_and_missing_data: list[str]
    follow_up_question: str | None = Field(
        description="One targeted question if the answer could materially change coaching advice; otherwise null."
    )
    next_step: str = Field(description="Conservative, practical recommendation for the next session.")
    plan_change_recommended: bool = Field(
        description="True only if the evidence justifies reviewing a future session; this does not authorize a change."
    )
    plan_change_rationale: str | None


METERS_PER_MILE = 1609.344
FEET_PER_METER = 3.280839895


def format_pace_per_mile(duration_s: float, distance_mi: float) -> str | None:
    """Pace as M:SS per mile."""
    if distance_mi <= 0:
        return None
    total_seconds = duration_s / distance_mi
    minutes = int(total_seconds // 60)
    seconds = int(round(total_seconds % 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}"


SYSTEM_PROMPT = """
You are a careful, evidence-aware endurance running coach reviewing one completed run.

Your job is to interpret the run against the planned session, the athlete's goals, recent comparable training,
the current training block, and the next few sessions.

Rules:
- Use only the supplied facts. Never invent weather, effort, heart rate, splits, medical history, or intent.
- Report distances in miles and paces as minutes:seconds per mile. Do not use kilometers or meters.
- Splits are the device laps, labeled with start_mi and end_mi. They may be unequal workout laps. Use their distance, pace_per_mile, and heart rate. Pace is already calculated from timer time, which excludes pauses.
- Explicitly distinguish observation from interpretation. A single unusual run is rarely enough to conclude that fitness changed.
- Consider route, elevation, weather, sensor quality, pacing, and missing data when supplied; do not assume they explain a result.
- Compare like with like where possible. Do not treat pace alone as effort, especially across hills or different conditions.
- Be concise, practical, and specific. Avoid generic praise and overconfident claims.
- Do not diagnose injury or illness. If the notes mention pain, concerning symptoms, or possible illness, prioritize conservative advice and recommend appropriate professional care when warranted.
- Do not recommend compensatory hard training, aggressive mileage increases, or making up missed workouts.
- Do not alter the plan. You may flag that a future session should be reviewed and explain why.
- Ask at most one high-value follow-up question. Ask none if the missing information would not materially change the advice.
- If the activity cannot be matched confidently to the plan, say so.
- The athlete may have performance goals, but health and sustainable progression take priority.
"""


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def activity_distance_mi(activity: dict[str, Any]) -> float:
    if activity.get("distance_mi") is not None:
        return float(activity["distance_mi"])
    if activity.get("distance_m") is not None:
        return float(activity["distance_m"]) / METERS_PER_MILE
    raise ValueError("Activity needs distance_mi.")


def validate_activity(activity: dict[str, Any]) -> None:
    required = ["activity_id", "started_at", "sport", "duration_s"]
    missing = [key for key in required if key not in activity]
    if missing:
        raise ValueError(f"Activity is missing required fields: {', '.join(missing)}")
    if activity_distance_mi(activity) <= 0 or activity["duration_s"] <= 0:
        raise ValueError("Distance and duration_s must be positive.")


def derive_metrics(activity: dict[str, Any]) -> dict[str, Any]:
    distance_mi = activity_distance_mi(activity)
    duration_s = float(activity["duration_s"])
    return {
        "distance_mi": round(distance_mi, 2),
        "duration_min": round(duration_s / 60, 1),
        "avg_pace_per_mile": activity.get("avg_pace_per_mile") or format_pace_per_mile(duration_s, distance_mi),
        "splits_provided": bool(activity.get("splits")),
        "split_length": (activity.get("import_metadata") or {}).get("split_length"),
    }


def split_for_coach(split: dict[str, Any]) -> dict[str, Any]:
    if split.get("distance_mi") is not None:
        distance_mi = float(split["distance_mi"])
    elif split.get("distance_m") is not None:
        distance_mi = float(split["distance_m"]) / METERS_PER_MILE
    else:
        distance_mi = None
    duration_s = split.get("duration_s")
    pace = split.get("pace_per_mile")
    if pace is None and distance_mi and duration_s:
        pace = format_pace_per_mile(float(duration_s), distance_mi)
    presented: dict[str, Any] = {
        "split": split.get("split"),
        "distance_mi": round(distance_mi, 2) if distance_mi is not None else None,
        "duration_s": duration_s,
        "pace_per_mile": pace,
        "avg_hr_bpm": split.get("avg_hr_bpm"),
    }
    if split.get("start_mi") is not None:
        presented["start_mi"] = split["start_mi"]
        presented["end_mi"] = split.get("end_mi")
    if split.get("elevation_gain_ft") is not None:
        presented["elevation_gain_ft"] = split["elevation_gain_ft"]
    elif split.get("elevation_gain_m") is not None:
        presented["elevation_gain_ft"] = round(float(split["elevation_gain_m"]) * FEET_PER_METER)
    return presented


def activity_for_coach(activity: dict[str, Any]) -> dict[str, Any]:
    presented = {
        key: value
        for key, value in activity.items()
        if key not in {"distance_m", "elevation_gain_m", "splits"}
    }
    presented["distance_mi"] = round(activity_distance_mi(activity), 2)
    if presented.get("elevation_gain_ft") is None and activity.get("elevation_gain_m") is not None:
        presented["elevation_gain_ft"] = round(float(activity["elevation_gain_m"]) * FEET_PER_METER)
    presented.pop("elevation_gain_m", None)
    presented["splits"] = [split_for_coach(split) for split in activity.get("splits") or []]
    presented["derived_metrics"] = derive_metrics(activity)
    return presented


def history_for_coach(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    presented: list[dict[str, Any]] = []
    for item in history:
        row = dict(item)
        if row.get("elevation_gain_ft") is None and row.get("elevation_gain_m") is not None:
            row["elevation_gain_ft"] = round(float(row["elevation_gain_m"]) * FEET_PER_METER)
        row.pop("elevation_gain_m", None)
        row.pop("distance_km", None)
        row.pop("avg_pace_min_per_km", None)
        presented.append(row)
    return presented


def build_context(
    activity: dict[str, Any],
    plan: dict[str, Any],
    profile: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "athlete_profile": profile,
        "planned_session": plan,
        "completed_activity": activity_for_coach(activity),
        "recent_history": history_for_coach(history[-12:]),
        "instructions": {
            "purpose": "Review this activity against the plan and athlete context.",
            "units": "miles and minutes per mile",
            "do_not_change_plan": True,
            "missing_data_policy": "Treat absent/null values as unknown, not as zero.",
        },
    }


def generate_review(
    context: dict[str, Any],
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> tuple[ReviewOutput, str]:
    """Ask the coach model for a structured review of one context packet."""
    model_name = model or os.getenv("OPENAI_MODEL", "gpt-5-mini")
    if client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is missing. Set it in .env, or use --dry-run.")
        client = OpenAI()
    response = client.responses.parse(
        model=model_name,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Review the following run. Return the required structured assessment. "
                    "Keep advice proportionate to the evidence.\n\n"
                    + json.dumps(context, indent=2, ensure_ascii=False)
                ),
            },
        ],
        text_format=ReviewOutput,
    )
    review = response.output_parsed
    if review is None:
        raise RuntimeError("The model did not return a parsed review.")
    return review, model_name


def render_email(review: ReviewOutput) -> str:
    sections = [
        review.summary,
        "",
        "What went well",
        *[f"- {item}" for item in review.what_went_well],
        "",
        "Compared with the plan",
        *[f"- {item}" for item in review.deviations_from_plan],
        "",
        "Coach's take",
        review.interpretation,
        "",
        "Next step",
        review.next_step,
    ]
    if review.uncertainty_and_missing_data:
        sections += ["", "What remains uncertain", *[f"- {item}" for item in review.uncertainty_and_missing_data]]
    if review.follow_up_question:
        sections += ["", "Quick question", review.follow_up_question]
    if review.plan_change_recommended and review.plan_change_rationale:
        sections += ["", "Plan review flag", review.plan_change_rationale]
    sections += ["", "— Your running coach (prototype; not a medical service)"]
    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review a completed run with an AI coach.")
    parser.add_argument("--activity", required=True, help="Path to normalized activity JSON.")
    parser.add_argument("--plan", required=True, help="Path to planned workout JSON.")
    parser.add_argument("--profile", required=True, help="Path to athlete profile JSON.")
    parser.add_argument("--history", help="Path to recent history JSON list.")
    parser.add_argument(
        "--history-from-supabase",
        action="store_true",
        help="Load recent activities from Supabase instead of a history file.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print context packet; do not call the model.")
    parser.add_argument("--output-dir", default="output", help="Directory for saved review files.")
    args = parser.parse_args()

    if args.history_from_supabase and args.history:
        parser.error("Use either --history or --history-from-supabase, not both.")
    if not args.history_from_supabase and not args.history:
        parser.error("--history is required unless --history-from-supabase is set.")

    load_dotenv()
    activity = load_json(args.activity)
    plan = load_json(args.plan)
    profile = load_json(args.profile)
    validate_activity(activity)

    if args.history_from_supabase:
        from run_coach.store import load_recent_activities, supabase_configured

        if not supabase_configured():
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for --history-from-supabase."
            )
        athlete_id = profile.get("athlete_id")
        if not athlete_id:
            raise ValueError("Athlete profile needs athlete_id to load history from Supabase.")
        history = load_recent_activities(str(athlete_id), exclude_activity_id=str(activity["activity_id"]))
    else:
        history = load_json(args.history)

    if not isinstance(history, list):
        raise ValueError("History must be a JSON list.")

    context = build_context(activity, plan, profile, history)
    if args.dry_run:
        print(json.dumps(context, indent=2, ensure_ascii=False))
        return

    review, model = generate_review(context)
    email_body = render_email(review)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c for c in str(activity["activity_id"]) if c.isalnum() or c in "-_") or "activity"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "activity_id": activity["activity_id"],
        "review": review.model_dump(),
        "email_body": email_body,
    }
    json_path = output_dir / f"{safe_id}_review.json"
    md_path = output_dir / f"{safe_id}_review.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(
        f"# {review.subject}\n\n{email_body}\n",
        encoding="utf-8",
    )
    print(f"Saved structured review: {json_path}")
    print(f"Saved email draft:      {md_path}")

    from run_coach.store import persist_review_if_configured

    if persist_review_if_configured(
        activity=activity,
        plan=plan,
        profile=profile,
        review=review.model_dump(),
        model=model,
        email_body=email_body,
    ):
        print("Saved review bundle to Supabase.")

    print("\n--- Email draft preview ---\n")
    print(f"Subject: {review.subject}\n\n{email_body}")


if __name__ == "__main__":
    main()
