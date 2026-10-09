from __future__ import annotations

from types import SimpleNamespace

import pytest

from run_coach.review import ReviewOutput, generate_review


def _sample_review() -> ReviewOutput:
    return ReviewOutput(
        subject="Easy day",
        summary="You finished the easy run.",
        what_went_well=["Completed the distance."],
        deviations_from_plan=[],
        interpretation="The effort matched an easy day.",
        uncertainty_and_missing_data=[],
        follow_up_question=None,
        next_step="Keep tomorrow easy.",
        plan_change_recommended=False,
        plan_change_rationale=None,
    )


def test_generate_review_parses_model_output() -> None:
    captured: dict = {}

    class FakeResponses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_parsed=_sample_review())

    class FakeClient:
        responses = FakeResponses()

    review, model = generate_review({"purpose": "test"}, client=FakeClient(), model="test-model")

    assert model == "test-model"
    assert review.subject == "Easy day"
    assert captured["model"] == "test-model"
    assert captured["text_format"] is ReviewOutput
    assert "test" in captured["input"][1]["content"]


def test_generate_review_rejects_empty_parse() -> None:
    class FakeResponses:
        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed=None)

    class FakeClient:
        responses = FakeResponses()

    with pytest.raises(RuntimeError, match="parsed review"):
        generate_review({}, client=FakeClient(), model="test-model")
