from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}
TPX_NS = {"tpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"}


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two GPS points."""
    earth_radius_m = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * earth_radius_m * math.asin(min(1.0, math.sqrt(a)))


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "gpx-activity"


def parse_gpx(path: str | Path, activity_id: str | None = None) -> dict[str, Any]:
    """
    Convert a GPX track to the run-coach normalized activity schema.

    Privacy: GPS coordinates and raw trackpoints are deliberately NOT written to
    the normalized output. Distance is summed from consecutive GPS points;
    duration is elapsed wall-clock time. This v0 importer does not infer moving
    time, because pauses and GPS jitter require additional validation.
    """
    path = Path(path)
    root = ET.parse(path).getroot()
    name = root.findtext(".//gpx:trk/gpx:name", default=path.stem, namespaces=GPX_NS)
    sport = root.findtext(".//gpx:trk/gpx:type", default="running", namespaces=GPX_NS)
    points = root.findall(".//gpx:trkseg/gpx:trkpt", GPX_NS)
    if len(points) < 2:
        raise ValueError("GPX needs at least two track points.")

    records: list[dict[str, Any]] = []
    for point in points:
        lat, lon = float(point.attrib["lat"]), float(point.attrib["lon"])
        time_el = point.findtext("gpx:time", namespaces=GPX_NS)
        ele_el = point.findtext("gpx:ele", namespaces=GPX_NS)
        hr_el = point.findtext(".//tpx:hr", namespaces=TPX_NS)
        cad_el = point.findtext(".//tpx:cad", namespaces=TPX_NS)
        records.append({
            "lat": lat,
            "lon": lon,
            "time": parse_time(time_el),
            "elevation_m": float(ele_el) if ele_el is not None else None,
            "hr": int(hr_el) if hr_el is not None else None,
            "cadence": int(cad_el) if cad_el is not None else None,
        })

    timed = [r for r in records if r["time"] is not None]
    if len(timed) < 2:
        raise ValueError("GPX needs at least two timestamped track points.")
    start, end = timed[0]["time"], timed[-1]["time"]
    elapsed_s = max(1, int((end - start).total_seconds()))

    # Distance by adjacent points. Large time gaps are flagged rather than
    # silently treated as accurate continuous movement.
    total_distance_m = 0.0
    elevation_gain_m = 0.0
    gap_count = 0
    segments: list[dict[str, Any]] = []
    for previous, current in zip(records, records[1:]):
        if previous["time"] is None or current["time"] is None:
            continue
        dt = (current["time"] - previous["time"]).total_seconds()
        if dt <= 0:
            continue
        distance_m = haversine_m(previous["lat"], previous["lon"], current["lat"], current["lon"])
        if dt > 20:
            gap_count += 1
        # Filter tiny GPS elevation fluctuations: count only positive changes > 3m.
        # This is a crude first pass, not a replacement for a trusted elevation source.
        e1, e2 = previous["elevation_m"], current["elevation_m"]
        if e1 is not None and e2 is not None and (e2 - e1) > 3:
            elevation_gain_m += e2 - e1
        segments.append({
            "distance_m": distance_m,
            "start_time": previous["time"],
            "end_time": current["time"],
            "dt_s": dt,
            "hr1": previous["hr"],
            "hr2": current["hr"],
            "cad1": previous["cadence"],
            "cad2": current["cadence"],
        })
        total_distance_m += distance_m

    hrs = [r["hr"] for r in records if r["hr"] is not None and 35 <= r["hr"] <= 230]
    cads = [r["cadence"] for r in records if r["cadence"] is not None and 0 < r["cadence"] <= 250]
    # GPX cadence conventions vary: some devices export one-leg cadence and
    # others total steps/min. Preserve raw average only as a metadata hint.
    splits: list[dict[str, Any]] = []
    km_target = 1000.0
    accumulated = 0.0
    split_start = start
    split_hr_weighted = 0.0
    split_hr_seconds = 0.0
    split_elev_gain = 0.0
    split_number = 1
    for segment in segments:
        remaining = segment["distance_m"]
        if remaining <= 0:
            continue
        # Spread segment duration and HR proportionally over any split boundary.
        while remaining > 0:
            needed = km_target - accumulated
            chunk = min(remaining, needed)
            fraction = chunk / segment["distance_m"]
            chunk_s = segment["dt_s"] * fraction
            hr_values = [v for v in (segment["hr1"], segment["hr2"]) if v is not None and 35 <= v <= 230]
            if hr_values:
                split_hr_weighted += mean(hr_values) * chunk_s
                split_hr_seconds += chunk_s
            accumulated += chunk
            remaining -= chunk
            if accumulated >= km_target - 1e-6:
                split_duration_s = max(1, int((segment["end_time"] - split_start).total_seconds()))
                splits.append({
                    "split": split_number,
                    "distance_m": int(round(km_target)),
                    "duration_s": split_duration_s,
                    "avg_hr_bpm": round(split_hr_weighted / split_hr_seconds) if split_hr_seconds else None,
                })
                split_number += 1
                split_start = segment["end_time"]
                accumulated = 0.0
                split_hr_weighted = 0.0
                split_hr_seconds = 0.0
                split_elev_gain = 0.0

    # Include a final partial kilometer if material.
    if accumulated >= 100:
        split_duration_s = max(1, int((end - split_start).total_seconds()))
        splits.append({
            "split": split_number,
            "distance_m": int(round(accumulated)),
            "duration_s": split_duration_s,
            "avg_hr_bpm": round(split_hr_weighted / split_hr_seconds) if split_hr_seconds else None,
        })

    return {
        "activity_id": activity_id or safe_id(path.stem),
        "started_at": start.isoformat(),
        "sport": sport or "running",
        "name": name,
        "distance_m": int(round(total_distance_m)),
        "duration_s": elapsed_s,
        "duration_type": "elapsed",
        "elevation_gain_m": int(round(elevation_gain_m)) if elevation_gain_m else None,
        "avg_hr_bpm": round(mean(hrs)) if hrs else None,
        "max_hr_bpm": max(hrs) if hrs else None,
        "avg_cadence_raw": round(mean(cads), 1) if cads else None,
        "perceived_effort_1_10": None,
        "conditions": None,
        "splits": splits,
        "notes": (
            f"Imported from GPX. Contains {len(records)} track points. "
            f"{gap_count} inter-point gaps exceeded 20 seconds. "
            "Distance is summed from GPS points; duration is elapsed time. "
            "Check these values against the watch/app summary before coaching."
        ),
        "import_metadata": {
            "source_format": "gpx",
            "trackpoint_count": len(records),
            "timestamped_point_count": len(timed),
            "gaps_over_20_seconds": gap_count,
            "gps_coordinates_stored": False,
            "distance_method": "sum of adjacent-point haversine distances",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert GPX into normalized run-coach JSON.")
    parser.add_argument("gpx", help="Input .gpx file")
    parser.add_argument("--output", "-o", help="Output JSON path; defaults to data/activities/<id>.json")
    parser.add_argument("--activity-id", help="Optional stable activity ID")
    args = parser.parse_args()

    activity = parse_gpx(args.gpx, args.activity_id)
    output = Path(args.output) if args.output else Path("data/activities") / f"{activity['activity_id']}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(activity, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Imported {activity['distance_m'] / 1000:.2f} km, {activity['duration_s'] / 60:.1f} min elapsed")
    print(f"Track points: {activity['import_metadata']['trackpoint_count']}; long gaps: {activity['import_metadata']['gaps_over_20_seconds']}")
    print(f"Saved normalized activity to {output}")
    print("Privacy: GPS coordinates were not included in the output JSON.")


if __name__ == "__main__":
    main()
