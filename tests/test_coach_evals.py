from __future__ import annotations

import json
import os
import re

import pytest
from dotenv import load_dotenv

from run_coach.review import ReviewOutput, build_context, generate_review

pytestmark = pytest.mark.llm

_REFUSAL = re.compile(r"\b(?:not|cannot|avoid|skip|instead|no)\b|don't|can't", re.IGNORECASE)


def _coaching_text(review: ReviewOutput) -> str:
    parts = [
        review.summary,
        review.interpretation,
        review.next_step,
        *review.what_went_well,
        *review.deviations_from_plan,
        *(review.uncertainty_and_missing_data or []),
        review.follow_up_question or "",
        review.plan_change_rationale or "",
    ]
    return "\n".join(parts).lower()


def _contains_unnegated(text: str, phrase: str) -> bool:
    """True when a sentence includes the phrase without refusing it."""
    needle = phrase.lower()
    if needle not in text.lower():
        return False
    for sentence in text.lower().replace("!", ".").replace("?", ".").split("."):
        if needle not in sentence:
            continue
        if _REFUSAL.search(sentence):
            continue
        return True
    return False


def _prescribes_makeup(next_step: str) -> bool:
    """True when a sentence tells the athlete to make up missed work."""
    return _contains_unnegated(next_step, "make up") or _contains_unnegated(next_step, "makeup")


def _assert_expectations(review: ReviewOutput, expect: dict) -> None:
    failures: list[str] = []
    if not review.summary.strip():
        failures.append("summary is empty")
    if not review.next_step.strip():
        failures.append("next_step is empty")
    if "plan_change_recommended" in expect and review.plan_change_recommended != expect["plan_change_recommended"]:
        failures.append(
            "plan_change_recommended is "
            f"{review.plan_change_recommended}, expected {expect['plan_change_recommended']}"
        )
    if expect.get("plan_change_recommended") and not (review.plan_change_rationale or "").strip():
        failures.append("plan_change_rationale is empty")
    if expect.get("follow_up_required") and not (review.follow_up_question or "").strip():
        failures.append("expected a follow-up question")
    if expect.get("deviations_required") and not review.deviations_from_plan:
        failures.append("expected at least one deviation from the plan")
    if expect.get("what_went_well_required") and not review.what_went_well:
        failures.append("expected at least one thing that went well")
    if expect.get("forbid_makeup_prescription") and _prescribes_makeup(review.next_step):
        failures.append("next step prescribes making up the missed work")

    next_step = review.next_step.lower()
    included = expect.get("next_step_must_include_any") or []
    if included and not any(phrase.lower() in next_step for phrase in included):
        failures.append(f"next step missing all of: {included}")
    for phrase in expect.get("next_step_forbidden_phrases") or []:
        if _contains_unnegated(review.next_step, phrase):
            failures.append(f"next step contains forbidden phrase: {phrase}")

    coaching = _coaching_text(review)
    for phrase in expect.get("forbidden_phrases") or []:
        if phrase.lower() in coaching:
            failures.append(f"review contains forbidden phrase: {phrase}")
    for group in expect.get("must_include_any_groups") or []:
        if not any(phrase.lower() in coaching for phrase in group):
            failures.append(f"review missing all of: {group}")

    if failures:
        rendered = json.dumps(review.model_dump(), indent=2, ensure_ascii=False)
        raise AssertionError("\n".join(failures) + "\n\nReview:\n" + rendered)


def test_negated_advice_is_not_a_prescription() -> None:
    assert _prescribes_makeup("Do not make up the missed reps.") is False
    assert _prescribes_makeup("Make up the remaining reps tomorrow.") is True
    assert _contains_unnegated("Don't go faster tomorrow.", "faster tomorrow") is False
    assert _contains_unnegated("Run faster tomorrow.", "faster tomorrow") is True


def test_coach_response(scenario: dict) -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY") or os.getenv("RUN_COACH_EVALS") != "1":
        pytest.skip("Set OPENAI_API_KEY and RUN_COACH_EVALS=1 to run live coach evals.")

    context = build_context(
        scenario["activity"],
        scenario["plan"],
        scenario["profile"],
        scenario["history"],
    )
    review, _model = generate_review(context)
    _assert_expectations(review, scenario["expect"])
