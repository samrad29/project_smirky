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


SYSTEM_PROMPT = """
You are a careful, evidence-aware endurance running coach reviewing one completed run.

Your job is to interpret the run against the planned session, the athlete's goals, recent comparable training,
the current training block, and the next few sessions.

Rules:
- Use only the supplied facts. Never invent weather, effort, heart rate, splits, medical history, or intent.
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


def validate_activity(activity: dict[str, Any]) -> None:
    required = ["activity_id", "started_at", "sport", "distance_m", "duration_s"]
    missing = [key for key in required if key not in activity]
    if missing:
        raise ValueError(f"Activity is missing required fields: {', '.join(missing)}")
    if activity["distance_m"] <= 0 or activity["duration_s"] <= 0:
        raise ValueError("distance_m and duration_s must be positive.")


def derive_metrics(activity: dict[str, Any]) -> dict[str, Any]:
    distance_m = float(activity["distance_m"])
    duration_s = float(activity["duration_s"])
    distance_km = distance_m / 1000
    duration_min = duration_s / 60
    pace_min_per_km = duration_min / distance_km
    pace_min_per_mile = pace_min_per_km * 1.609344
    return {
        "distance_km": round(distance_km, 2),
        "duration_min": round(duration_min, 1),
        "avg_pace_min_per_km": round(pace_min_per_km, 2),
        "avg_pace_min_per_mile": round(pace_min_per_mile, 2),
        "splits_provided": bool(activity.get("splits")),
    }


def build_context(
    activity: dict[str, Any],
    plan: dict[str, Any],
    profile: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "athlete_profile": profile,
        "planned_session": plan,
        "completed_activity": {
            **activity,
            "derived_metrics": derive_metrics(activity),
        },
        "recent_history": history[-12:],
        "instructions": {
            "purpose": "Review this activity against the plan and athlete context.",
            "do_not_change_plan": True,
            "missing_data_policy": "Treat absent/null values as unknown, not as zero.",
        },
    }


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
    parser.add_argument("--history", required=True, help="Path to recent history JSON list.")
    parser.add_argument("--dry-run", action="store_true", help="Print context packet; do not call the model.")
    parser.add_argument("--output-dir", default="output", help="Directory for saved review files.")
    args = parser.parse_args()

    load_dotenv()
    activity = load_json(args.activity)
    plan = load_json(args.plan)
    profile = load_json(args.profile)
    history = load_json(args.history)

    validate_activity(activity)
    if not isinstance(history, list):
        raise ValueError("History file must contain a JSON list.")

    context = build_context(activity, plan, profile, history)
    if args.dry_run:
        print(json.dumps(context, indent=2, ensure_ascii=False))
        return

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing. Set it in .env, or use --dry-run.")

    model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    client = OpenAI()
    response = client.responses.parse(
        model=model,
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

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c for c in str(activity["activity_id"]) if c.isalnum() or c in "-_") or "activity"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "activity_id": activity["activity_id"],
        "review": review.model_dump(),
        "email_body": render_email(review),
    }
    json_path = output_dir / f"{safe_id}_review.json"
    md_path = output_dir / f"{safe_id}_review.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(
        f"# {review.subject}\n\n{render_email(review)}\n",
        encoding="utf-8",
    )
    print(f"Saved structured review: {json_path}")
    print(f"Saved email draft:      {md_path}")
    print("\n--- Email draft preview ---\n")
    print(f"Subject: {review.subject}\n\n{render_email(review)}")


if __name__ == "__main__":
    main()
