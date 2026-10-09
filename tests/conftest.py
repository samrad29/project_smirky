from __future__ import annotations

import json
from pathlib import Path

import pytest

SCENARIO_DIR = Path(__file__).parent / "scenarios"


def load_scenarios() -> list[dict]:
    scenarios = []
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        scenarios.append(json.loads(path.read_text(encoding="utf-8")))
    return scenarios


@pytest.fixture(params=load_scenarios(), ids=lambda scenario: scenario["id"])
def scenario(request: pytest.FixtureRequest) -> dict:
    return request.param
