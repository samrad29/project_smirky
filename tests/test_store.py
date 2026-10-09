from __future__ import annotations

from run_coach.store import (
    activity_to_history_row,
    load_recent_activities,
    persist_review_if_configured,
    save_review_bundle,
    supabase_configured,
)


class Query:
    def __init__(self, recorder: Recorder, name: str) -> None:
        self.recorder = recorder
        self.name = name
        self.operation: str | None = None
        self.payload: dict | None = None
        self.on_conflict: str | None = None
        self.filters: list[tuple[str, str]] = []
        self.limit_n: int | None = None

    def upsert(self, payload: dict, on_conflict: str | None = None) -> Query:
        self.operation = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def insert(self, payload: dict) -> Query:
        self.operation = "insert"
        self.payload = payload
        return self

    def select(self, columns: str) -> Query:
        self.operation = "select"
        self.columns = columns
        return self

    def eq(self, column: str, value: str) -> Query:
        self.filters.append((column, value))
        return self

    def order(self, column: str, desc: bool = False) -> Query:
        self.order_by = (column, desc)
        return self

    def limit(self, count: int) -> Query:
        self.limit_n = count
        return self

    def execute(self) -> object:
        self.recorder.calls.append(self)
        data = self.recorder.rows if self.operation == "select" else [self.payload]
        return type("Response", (), {"data": data})()


class Recorder:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.calls: list[Query] = []
        self.rows = rows or []

    def table(self, name: str) -> Query:
        return Query(self, name)


def test_save_conversation_and_profile_change_rows() -> None:
    from run_coach.store import save_conversation_message, save_profile_change

    recorder = Recorder()
    profile = {"athlete_id": "scenario-runner", "goals": ["finish"]}
    save_conversation_message(
        profile=profile,
        role="user",
        content="The run felt flat.",
        created_at="2026-06-12T18:00:00+00:00",
        client=recorder,
    )
    save_profile_change(
        profile=profile,
        entry={
            "field": "goals",
            "old_value": ["finish"],
            "new_value": ["finish", "10k"],
            "reason": "The athlete added a 10k.",
            "status": "applied",
            "created_at": "2026-06-12T18:01:00+00:00",
        },
        client=recorder,
    )

    assert [(call.name, call.operation) for call in recorder.calls] == [
        ("athletes", "upsert"),
        ("conversation_messages", "insert"),
        ("athletes", "upsert"),
        ("profile_changes", "insert"),
    ]
    assert recorder.calls[1].payload["content"] == "The run felt flat."
    assert recorder.calls[3].payload["status"] == "applied"


def test_supabase_configured_requires_both_values(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    assert supabase_configured() is False
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    assert supabase_configured() is False
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    assert supabase_configured() is True


def test_persist_skips_without_credentials(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    saved = persist_review_if_configured(
        activity={"activity_id": "a"},
        plan={},
        profile={"athlete_id": "runner"},
        review={},
        model="test",
        email_body="hello",
    )
    assert saved is False


def test_save_review_bundle_writes_each_table() -> None:
    recorder = Recorder()
    activity = {"activity_id": "run-1", "started_at": "2026-06-12T17:40:00-05:00", "distance_mi": 8}
    plan = {"date": "2026-06-12", "session_type": "easy run"}
    profile = {"athlete_id": "scenario-runner", "goals": ["finish"]}
    save_review_bundle(
        activity=activity,
        plan=plan,
        profile=profile,
        review={"summary": "Fine."},
        model="test-model",
        email_body="Fine.",
        client=recorder,
    )

    assert [call.name for call in recorder.calls] == [
        "athletes",
        "activities",
        "planned_sessions",
        "reviews",
    ]
    assert [call.operation for call in recorder.calls] == ["upsert", "upsert", "upsert", "insert"]
    assert recorder.calls[0].on_conflict == "athlete_id"
    assert recorder.calls[1].payload["payload"] == activity
    assert recorder.calls[2].on_conflict == "activity_id"
    assert recorder.calls[2].payload["session_date"] == "2026-06-12"
    assert recorder.calls[3].payload["model"] == "test-model"
    assert recorder.calls[3].payload["email_body"] == "Fine."


def test_activity_to_history_row_uses_coach_summary_fields() -> None:
    row = activity_to_history_row(
        {
            "activity_id": "run-1",
            "started_at": "2026-06-12T17:40:00-05:00",
            "sport": "run",
            "distance_mi": 8,
            "avg_pace_per_mile": "9:09",
            "notes": "Cut it short.",
            "perceived_effort_1_10": None,
        }
    )
    assert row["date"] == "2026-06-12"
    assert row["session_type"] == "run"
    assert row["avg_pace_per_mile"] == "9:09"
    assert "perceived_effort_1_10" not in row


def test_load_recent_activities_accepts_json_string_payloads() -> None:
    recorder = Recorder(
        rows=[
            {
                "activity_id": "older",
                "started_at": "2026-06-14T12:00:00-05:00",
                "payload": '{"activity_id": "older", "started_at": "2026-06-14T12:00:00-05:00", "sport": "run", "notes": "From a string."}',
            }
        ]
    )
    history = load_recent_activities("scenario-runner", client=recorder)
    assert history[0]["notes"] == "From a string."
    assert history[0]["date"] == "2026-06-14"


def test_load_recent_activities_is_oldest_first_and_skips_current() -> None:
    recorder = Recorder(
        rows=[
            {
                "activity_id": "current",
                "started_at": "2026-06-18T12:00:00-05:00",
                "payload": {
                    "activity_id": "current",
                    "started_at": "2026-06-18T12:00:00-05:00",
                    "sport": "run",
                    "distance_mi": 6,
                    "notes": "Today.",
                },
            },
            {
                "activity_id": "newer",
                "started_at": "2026-06-16T12:00:00-05:00",
                "payload": {
                    "activity_id": "newer",
                    "started_at": "2026-06-16T12:00:00-05:00",
                    "name": "Easy run",
                    "distance_mi": 6,
                    "notes": "Newer.",
                },
            },
            {
                "activity_id": "older",
                "started_at": "2026-06-14T12:00:00-05:00",
                "payload": {
                    "activity_id": "older",
                    "started_at": "2026-06-14T12:00:00-05:00",
                    "sport": "run",
                    "distance_mi": 5,
                    "notes": "Older.",
                },
            },
        ]
    )
    history = load_recent_activities(
        "scenario-runner",
        limit=12,
        exclude_activity_id="current",
        client=recorder,
    )
    assert [item["activity_id"] for item in history] == ["older", "newer"]
    assert history[0]["notes"] == "Older."
    assert recorder.calls[0].filters == [("athlete_id", "scenario-runner")]
    assert recorder.calls[0].order_by == ("started_at", True)
    assert recorder.calls[0].limit_n == 13
