from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from run_coach.garmin_sync import (
    fit_bytes_from_download,
    pull_new_activities,
    select_new_activities,
    sync_new_activities,
)


class FakeGarmin:
    def __init__(self, activities: list[dict]) -> None:
        self.activities = activities
        self.requested: list[tuple[str, object]] = []

    def get_activities(self, start: int, limit: int) -> list[dict]:
        return self.activities[start : start + limit]

    def download_activity(self, activity_id: str, dl_fmt: object = None) -> bytes:
        self.requested.append((str(activity_id), dl_fmt))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(f"{activity_id}.fit", b"fit-data")
        return buffer.getvalue()


def test_select_new_activities_stops_at_the_saved_watermark() -> None:
    remote = [
        {"activityId": 30, "startTimeLocal": "2026-10-15 08:00:00"},
        {"activityId": 20, "startTimeLocal": "2026-10-10 08:00:00"},
        {"activityId": 10, "startTimeLocal": "2026-10-08 17:15:35"},
    ]
    chosen = select_new_activities(remote, set(), "2026-10-08T17:15:35")
    assert [item["activityId"] for item in chosen] == [30, 20]


def test_select_new_activities_skips_ids_already_downloaded() -> None:
    remote = [
        {"activityId": 30, "startTimeLocal": "2026-10-15 08:00:00"},
        {"activityId": 20, "startTimeLocal": "2026-10-10 08:00:00"},
        {"activityId": 10, "startTimeLocal": "2026-10-08 17:15:35"},
    ]
    chosen = select_new_activities(remote, {"30"}, "2026-10-08T17:15:35")
    assert [item["activityId"] for item in chosen] == [20]


def test_sync_downloads_only_new_fit_files_and_remembers_them(tmp_path: Path) -> None:
    inbox = tmp_path / "FIT_files"
    state = tmp_path / "downloaded_ids.json"
    remote = [
        {"activityId": 30, "startTimeLocal": "2026-10-15 08:00:00"},
        {"activityId": 20, "startTimeLocal": "2026-10-10 08:00:00"},
        {"activityId": 10, "startTimeLocal": "2026-10-08 17:15:35"},
    ]
    client = FakeGarmin(remote)
    saved_activities = [{"activity_id": "local", "started_at": "2026-10-08T17:15:35-05:00"}]

    first = sync_new_activities(client, inbox=inbox, activities=saved_activities, state_path=state)
    second = sync_new_activities(client, inbox=inbox, activities=saved_activities, state_path=state)

    assert first.downloaded == ["30", "20"]
    assert (inbox / "30.fit").read_bytes() == b"fit-data"
    assert second.downloaded == []
    assert [activity_id for activity_id, _fmt in client.requested] == ["30", "20"]


def test_fit_bytes_from_download_reads_the_zip() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("activity.fit", b"raw-fit")
    assert fit_bytes_from_download(buffer.getvalue()) == b"raw-fit"


def test_pull_skips_when_garmin_is_not_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    note = pull_new_activities(tmp_path / "inbox", tmp_path / "activities", token_dir=tmp_path / "empty-tokens")
    assert note is None


def test_sync_watermark_uses_newest_saved_activity() -> None:
    from run_coach.garmin_sync import sync_watermark

    activities = [
        {"started_at": "2026-10-08T17:15:35-05:00"},
        {"started_at": "2026-10-01T08:00:00-05:00"},
    ]
    assert sync_watermark(activities) == "2026-10-08T17:15:35"
    assert sync_watermark([], today=datetime(2026, 10, 15)) == "2026-10-01T00:00:00"
