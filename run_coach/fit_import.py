from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fitparse import FitFile, FitParseError

METERS_PER_MILE = 1609.344
FEET_PER_METER = 3.280839895
INBOX_DIR = Path("data/FIT_files")
ARCHIVE_DIR = INBOX_DIR / "old_files"
OUTPUT_DIR = Path("data/activities")


def format_pace_per_mile(duration_s: float, distance_mi: float) -> str | None:
    """Pace as M:SS per mile."""
    if distance_mi <= 0 or duration_s <= 0:
        return None
    total_seconds = duration_s / distance_mi
    minutes = int(total_seconds // 60)
    seconds = int(round(total_seconds % 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}"


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "fit-activity"


def message_fields(message: Any) -> dict[str, Any]:
    return {field.name: field.value for field in message}


def miles(distance_m: float) -> float:
    return round(distance_m / METERS_PER_MILE, 2)


def feet(distance_m: float | None) -> int | None:
    if distance_m is None:
        return None
    return int(round(float(distance_m) * FEET_PER_METER))


def local_start_iso(start_utc: datetime, activity_fields: dict[str, Any]) -> str:
    """FIT timestamps are UTC. Shift the start by the activity's local offset when present."""
    utc_end = activity_fields.get("timestamp")
    local_end = activity_fields.get("local_timestamp")
    if isinstance(utc_end, datetime) and isinstance(local_end, datetime):
        offset: timedelta = local_end - utc_end
        local_start = start_utc + offset
        total_minutes = int(offset.total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        absolute = abs(total_minutes)
        hours, minutes = divmod(absolute, 60)
        return local_start.strftime("%Y-%m-%dT%H:%M:%S") + f"{sign}{hours:02d}:{minutes:02d}"
    return start_utc.isoformat() + "Z"


def lap_to_split(lap: dict[str, Any], split_number: int, covered_m: float) -> tuple[dict[str, Any], float]:
    distance_m = float(lap["total_distance"])
    timer_s = float(lap["total_timer_time"])
    elapsed_s = float(lap.get("total_elapsed_time") or timer_s)
    start_mi = round(covered_m / METERS_PER_MILE, 2)
    covered_m += distance_m
    split: dict[str, Any] = {
        "split": split_number,
        "start_mi": start_mi,
        "end_mi": round(covered_m / METERS_PER_MILE, 2),
        "distance_mi": miles(distance_m),
        "duration_s": max(1, int(round(timer_s))),
        "elapsed_s": max(1, int(round(elapsed_s))),
        "pace_per_mile": format_pace_per_mile(timer_s, distance_m / METERS_PER_MILE),
        "avg_hr_bpm": lap.get("avg_heart_rate"),
        "max_hr_bpm": lap.get("max_heart_rate"),
        "avg_cadence": lap.get("avg_running_cadence"),
        "elevation_gain_ft": feet(lap.get("total_ascent")),
        "lap_trigger": lap.get("lap_trigger"),
    }
    paused_s = int(round(elapsed_s - timer_s))
    if paused_s >= 1:
        split["paused_s"] = paused_s
    return split, covered_m


def parse_fit(path: str | Path, activity_id: str | None = None) -> dict[str, Any]:
    """
    Convert a FIT activity to the run-coach schema using device session and lap summaries.

    GPS coordinates from record and lap messages are not written to the output.
    Pace uses device timer time, which excludes pauses. Elapsed time is kept alongside it.
    """
    path = Path(path)
    fit = FitFile(str(path))
    sessions = [message_fields(message) for message in fit.get_messages("session")]
    if not sessions:
        raise ValueError("FIT file has no session summary.")
    session = sessions[0]
    if session.get("total_distance") in (None, 0) or session.get("total_timer_time") in (None, 0):
        raise ValueError("FIT session needs a positive distance and timer time.")
    if not isinstance(session.get("start_time"), datetime):
        raise ValueError("FIT session has no start time.")

    sports = [message_fields(message) for message in fit.get_messages("sport")]
    activities = [message_fields(message) for message in fit.get_messages("activity")]
    sport_name = (sports[0].get("name") if sports else None) or session.get("sport") or "running"
    activity_fields = activities[0] if activities else {}

    all_laps = [message_fields(message) for message in fit.get_messages("lap")]
    first_lap = int(session.get("first_lap_index") or 0)
    lap_count = int(session.get("num_laps") or len(all_laps))
    selected = [
        lap for lap in all_laps[first_lap:first_lap + lap_count]
        if lap.get("total_distance") not in (None, 0) and lap.get("total_timer_time") not in (None, 0)
    ]

    splits: list[dict[str, Any]] = []
    covered_m = 0.0
    for index, lap in enumerate(selected, start=1):
        split, covered_m = lap_to_split(lap, index, covered_m)
        splits.append(split)

    distance_m = float(session["total_distance"])
    timer_s = float(session["total_timer_time"])
    elapsed_s = float(session.get("total_elapsed_time") or timer_s)
    paused_s = int(round(elapsed_s - timer_s))
    distance_mi = miles(distance_m)

    return {
        "activity_id": activity_id or safe_id(path.stem),
        "started_at": local_start_iso(session["start_time"], activity_fields),
        "sport": session.get("sport") or "running",
        "name": sport_name,
        "distance_mi": distance_mi,
        "duration_s": max(1, int(round(timer_s))),
        "elapsed_s": max(1, int(round(elapsed_s))),
        "paused_s": paused_s if paused_s >= 1 else 0,
        "duration_type": "timer",
        "avg_pace_per_mile": format_pace_per_mile(timer_s, distance_m / METERS_PER_MILE),
        "elevation_gain_ft": feet(session.get("total_ascent")),
        "avg_hr_bpm": session.get("avg_heart_rate"),
        "max_hr_bpm": session.get("max_heart_rate"),
        "avg_cadence_raw": session.get("avg_running_cadence"),
        "perceived_effort_1_10": None,
        "conditions": None,
        "splits": splits,
        "notes": (
            f"Imported from FIT. Device recorded {len(splits)} lap(s). "
            "Pace uses timer time and excludes pauses; elapsed time includes them. "
            "GPS coordinates were not stored."
        ),
        "import_metadata": {
            "source_format": "fit",
            "lap_count": len(splits),
            "lap_source": "device laps",
            "split_length": "device lap",
            "pace_unit": "minutes per mile",
            "distance_source": "session total_distance",
            "duration_source": "session total_timer_time",
            "gps_coordinates_stored": False,
        },
    }


def fit_files_in(inbox: Path) -> list[Path]:
    """FIT files sitting directly in the inbox. Archived files live in a subfolder."""
    return sorted(
        path for path in inbox.iterdir()
        if path.is_file() and path.suffix.lower() == ".fit"
    )


def unique_activity_path(output_dir: Path, activity_id: str) -> tuple[Path, str]:
    candidate = output_dir / f"{activity_id}.json"
    if not candidate.exists():
        return candidate, activity_id
    suffix = 2
    while True:
        next_id = f"{activity_id}-{suffix}"
        candidate = output_dir / f"{next_id}.json"
        if not candidate.exists():
            return candidate, next_id
        suffix += 1


def archive_fit(path: Path, archive_dir: Path) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / path.name
    if destination.exists():
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        destination = archive_dir / f"{path.stem}-{stamp}{path.suffix}"
    return path.replace(destination)


def import_inbox(
    inbox: Path = INBOX_DIR,
    archive_dir: Path = ARCHIVE_DIR,
    output_dir: Path = OUTPUT_DIR,
) -> int:
    """Import each FIT file in the inbox, then move successful files into old_files."""
    if not inbox.is_dir():
        print(f"FIT inbox not found: {inbox}")
        return 1

    files = fit_files_in(inbox)
    if not files:
        print(f"No new FIT files in {inbox}")
        return 0

    imported = 0
    for path in files:
        try:
            activity = parse_fit(path)
            output, activity_id = unique_activity_path(output_dir, activity["activity_id"])
            activity["activity_id"] = activity_id
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(activity, indent=2, ensure_ascii=False), encoding="utf-8")
            archived = archive_fit(path, archive_dir)
        except (FitParseError, OSError, ValueError) as exc:
            print(f"Left {path.name} in {inbox}: {exc}")
            continue
        imported += 1
        print(
            f"Imported {activity['distance_mi']:.2f} mi, "
            f"{activity['duration_s'] / 60:.1f} min timer "
            f"({activity['elapsed_s'] / 60:.1f} min elapsed), "
            f"{activity['import_metadata']['lap_count']} laps"
        )
        for split in activity["splits"]:
            print(
                f"  Lap {split['split']}: {split['start_mi']:.2f}-{split['end_mi']:.2f} mi, "
                f"{split['pace_per_mile']}/mi, HR {split['avg_hr_bpm']}"
            )
        print(f"Saved normalized activity to {output}")
        print(f"Moved {path.name} to {archived}")

    print(f"Imported {imported} of {len(files)} file(s).")
    print("Privacy: GPS coordinates were not included in the output JSON.")
    return 0 if imported == len(files) else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert every new FIT file in data/FIT_files into normalized run-coach JSON."
    )
    parser.parse_args()
    raise SystemExit(import_inbox())


if __name__ == "__main__":
    main()
