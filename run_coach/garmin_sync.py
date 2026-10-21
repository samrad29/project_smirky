from __future__ import annotations

import io
import json
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

STATE_PATH = Path("data/garmin/downloaded_ids.json")
TOKEN_DIR = Path("data/garmin/tokens")
PAGE_SIZE = 20
MAX_PAGES = 5
FIRST_SYNC_LOOKBACK_DAYS = 14


class GarminSyncError(RuntimeError):
    """Garmin credentials or a download could not be used."""


@dataclass(frozen=True)
class SyncResult:
    downloaded: list[str]

    @property
    def message(self) -> str:
        if not self.downloaded:
            return "No new Garmin activities."
        names = ", ".join(f"{activity_id}.fit" for activity_id in self.downloaded)
        return f"Downloaded {len(self.downloaded)} new Garmin activity file(s): {names}."


def garmin_configured(token_dir: Path = TOKEN_DIR) -> bool:
    if os.getenv("GARMIN_EMAIL") and os.getenv("GARMIN_PASSWORD"):
        return True
    return token_dir.is_dir() and any(token_dir.iterdir())


def local_timestamp(value: str) -> str:
    """Comparable local timestamp, YYYY-MM-DDTHH:MM:SS, without a zone suffix."""
    text = value.strip().replace(" ", "T")
    return text[:19]


def newest_local_timestamp(activities: list[dict[str, Any]]) -> str | None:
    stamps = [local_timestamp(str(item["started_at"])) for item in activities if item.get("started_at")]
    return max(stamps) if stamps else None


def sync_watermark(activities: list[dict[str, Any]], *, today: datetime | None = None) -> str:
    """Do not download the whole account. Start after the newest saved activity."""
    newest = newest_local_timestamp(activities)
    if newest:
        return newest
    current = today or datetime.now()
    return (current - timedelta(days=FIRST_SYNC_LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00")


def load_downloaded_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(activity_id) for activity_id in payload.get("downloaded_ids", [])}


def save_downloaded_ids(path: Path, activity_ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"downloaded_ids": sorted(activity_ids)}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def select_new_activities(
    remote_newest_first: list[dict[str, Any]],
    downloaded_ids: set[str],
    watermark: str,
) -> list[dict[str, Any]]:
    """Keep Garmin activities newer than the watermark and not already downloaded."""
    selected: list[dict[str, Any]] = []
    for activity in remote_newest_first:
        started = activity.get("startTimeLocal")
        if not started or local_timestamp(str(started)) <= watermark:
            break
        activity_id = str(activity.get("activityId"))
        if activity_id in downloaded_ids:
            continue
        selected.append(activity)
    return selected


def fit_bytes_from_download(payload: bytes) -> bytes:
    """Garmin's original download is a zip that contains the FIT file."""
    if payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".fit")]
            if not names:
                raise GarminSyncError("Garmin download did not contain a FIT file.")
            return archive.read(names[0])
    if not payload:
        raise GarminSyncError("Garmin download was empty.")
    return payload


def fetch_recent_activities(client: Any, watermark: str) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        batch = client.get_activities(page * PAGE_SIZE, PAGE_SIZE) or []
        if not isinstance(batch, list):
            raise GarminSyncError("Garmin activity list was not a list.")
        collected.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        if any(
            not item.get("startTimeLocal") or local_timestamp(str(item["startTimeLocal"])) <= watermark
            for item in batch
        ):
            break
    return collected


def _download_original(client: Any, activity_id: str) -> bytes:
    from garminconnect import Garmin

    return client.download_activity(activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)


def already_saved(activity_id: str, inbox: Path) -> bool:
    name = f"{activity_id}.fit"
    return (inbox / name).exists() or (inbox / "old_files" / name).exists()


def sync_new_activities(
    client: Any,
    *,
    inbox: Path,
    activities: list[dict[str, Any]],
    state_path: Path,
    today: datetime | None = None,
) -> SyncResult:
    watermark = sync_watermark(activities, today=today)
    remote = fetch_recent_activities(client, watermark)
    downloaded_ids = load_downloaded_ids(state_path)
    chosen = select_new_activities(remote, downloaded_ids, watermark)
    inbox.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for activity in chosen:
        activity_id = str(activity["activityId"])
        if already_saved(activity_id, inbox):
            downloaded_ids.add(activity_id)
            continue
        payload = _download_original(client, activity_id)
        fit_bytes = fit_bytes_from_download(payload)
        (inbox / f"{activity_id}.fit").write_bytes(fit_bytes)
        downloaded_ids.add(activity_id)
        saved.append(activity_id)
    save_downloaded_ids(state_path, downloaded_ids)
    return SyncResult(saved)


def connect_garmin(token_dir: Path = TOKEN_DIR, *, prompt_mfa: Callable[[], str] | None = None) -> Any:
    from garminconnect import Garmin

    email = os.getenv("GARMIN_EMAIL") or None
    password = os.getenv("GARMIN_PASSWORD") or None
    token_dir.mkdir(parents=True, exist_ok=True)
    client = Garmin(email, password, prompt_mfa=prompt_mfa, return_on_mfa=prompt_mfa is None)
    status, _token = client.login(str(token_dir))
    if status == "needs_mfa":
        raise GarminSyncError(
            "Garmin needs a one-time MFA code. Run python -m run_coach.garmin_sync and enter the code."
        )
    return client


def pull_new_activities(
    inbox: Path,
    activities_dir: Path,
    *,
    state_path: Path = STATE_PATH,
    token_dir: Path = TOKEN_DIR,
    client: Any | None = None,
) -> str | None:
    """Download new FIT files when Garmin is configured. Return None when it is not."""
    if client is None and not garmin_configured(token_dir):
        return None
    from run_coach.activities import load_activities

    try:
        garmin = client if client is not None else connect_garmin(token_dir)
        result = sync_new_activities(
            garmin,
            inbox=inbox,
            activities=load_activities(activities_dir),
            state_path=state_path,
        )
    except GarminSyncError as exc:
        return f"Garmin sync failed: {exc}"
    except Exception as exc:
        return f"Garmin sync failed: {exc.__class__.__name__}: {exc}"
    return result.message


def main() -> None:
    from dotenv import load_dotenv

    from run_coach.activities import load_activities

    load_dotenv()
    if not garmin_configured():
        raise SystemExit("Set GARMIN_EMAIL and GARMIN_PASSWORD in .env to download new activities.")
    result = pull_new_activities(
        Path("data/FIT_files"),
        Path("data/activities"),
        client=connect_garmin(prompt_mfa=lambda: input("Garmin MFA code: ").strip()),
    )
    print(result)


if __name__ == "__main__":
    main()
