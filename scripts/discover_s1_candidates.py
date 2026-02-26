#!/usr/bin/env python3
"""Discover Sentinel-1 acquisition geometries over an AOI.

Why:
    Before fixing a single orbit/direction in project config, quickly inspect
    which geometry groups provide enough temporal coverage for stack creation.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tomllib
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


@dataclass
class GeometrySummary:
    """Aggregated geometry statistics from ASF search results."""

    flight_direction: str
    relative_orbit: int
    frame_number: str
    beam_mode: str
    polarization: str
    scene_count: int
    unique_dates: int
    first_date: str
    last_date: str
    total_gb_decimal: float


def read_toml(path: Path) -> dict[str, Any]:
    """Read TOML config from disk."""
    with path.open("rb") as f:
        return tomllib.load(f)


def resolve_path(repo_root: Path, value: str) -> Path:
    """Resolve a path relative to repo root when needed."""
    p = Path(value)
    return p if p.is_absolute() else (repo_root / p).resolve()


def parse_kml_to_wkt(kml_path: Path) -> str:
    """Convert polygon coordinates in KML to WKT polygon."""
    root = ET.parse(kml_path).getroot()
    coordinates = root.find(".//kml:coordinates", KML_NS)
    if coordinates is None or not coordinates.text:
        raise ValueError(f"No polygon coordinates found in KML: {kml_path}")

    points: list[tuple[float, float]] = []
    for token in coordinates.text.strip().split():
        lon, lat, *_ = token.split(",")
        points.append((float(lon), float(lat)))

    if len(points) < 4:
        raise ValueError(f"Invalid polygon in KML (too few points): {kml_path}")
    if points[0] != points[-1]:
        points = [*points, points[0]]
    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in points) + "))"


def enum_or_fail(enum_obj: Any, value: str, field_name: str):
    """Resolve enum name from asf_search enum container."""
    try:
        return getattr(enum_obj, value)
    except AttributeError as exc:
        raise ValueError(f"Invalid {field_name}: {value}") from exc


def to_int(value: Any, default: int = 0) -> int:
    """Convert value to integer with fallback."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_summaries(results: list[Any]) -> list[GeometrySummary]:
    """Aggregate search results by geometry group.

    Group key:
        (flight_direction, relative_orbit, frame_number)
    """
    groups: dict[tuple[str, int, str], dict[str, Any]] = defaultdict(
        lambda: {
            "dates": set(),
            "scene_count": 0,
            "bytes": 0,
            "beam_mode": "",
            "polarization": set(),
        }
    )

    for item in results:
        props = item.properties
        flight = str(props.get("flightDirection", "UNKNOWN")).upper()
        rel_orbit = to_int(
            props.get("pathNumber", props.get("relativeOrbit", 0)),
            default=0,
        )
        frame = str(props.get("frameNumber", ""))
        key = (flight, rel_orbit, frame)

        g = groups[key]
        g["scene_count"] += 1
        start_time = str(props.get("startTime", ""))
        if len(start_time) >= 10:
            g["dates"].add(start_time[:10])
        g["beam_mode"] = str(props.get("beamModeType", ""))
        pol = str(props.get("polarization", "")).strip()
        if pol:
            g["polarization"].add(pol)
        g["bytes"] += to_int(props.get("bytes", 0), default=0)

    out: list[GeometrySummary] = []
    for (flight, rel_orbit, frame), g in groups.items():
        dates = sorted(g["dates"])
        out.append(
            GeometrySummary(
                flight_direction=flight,
                relative_orbit=rel_orbit,
                frame_number=frame,
                beam_mode=g["beam_mode"],
                polarization=",".join(sorted(g["polarization"])) if g["polarization"] else "",
                scene_count=int(g["scene_count"]),
                unique_dates=len(dates),
                first_date=dates[0] if dates else "",
                last_date=dates[-1] if dates else "",
                total_gb_decimal=round(float(g["bytes"]) / 1e9, 2),
            )
        )

    out.sort(
        key=lambda s: (
            -s.unique_dates,
            -s.scene_count,
            s.flight_direction,
            s.relative_orbit,
            s.frame_number,
        )
    )
    return out


def print_table(rows: list[GeometrySummary], max_rows: int) -> None:
    """Print ranked geometry rows as a compact table."""
    shown = rows[:max_rows] if max_rows > 0 else rows
    header = (
        f"{'rank':>4}  {'dir':<4}  {'orbit':>5}  {'frame':>6}  "
        f"{'dates':>5}  {'scenes':>6}  {'first':<10}  {'last':<10}  {'GB':>7}"
    )
    print(header)
    print("-" * len(header))
    for idx, row in enumerate(shown, start=1):
        print(
            f"{idx:>4}  {row.flight_direction:<4}  {row.relative_orbit:>5}  "
            f"{row.frame_number:>6}  {row.unique_dates:>5}  {row.scene_count:>6}  "
            f"{row.first_date:<10}  {row.last_date:<10}  {row.total_gb_decimal:>7.2f}"
        )


def write_csv_rows(path: Path, rows: list[GeometrySummary]) -> None:
    """Write geometry summary table to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "flight_direction",
                "relative_orbit",
                "frame_number",
                "beam_mode",
                "polarization",
                "scene_count",
                "unique_dates",
                "first_date",
                "last_date",
                "total_gb_decimal",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.flight_direction,
                    row.relative_orbit,
                    row.frame_number,
                    row.beam_mode,
                    row.polarization,
                    row.scene_count,
                    row.unique_dates,
                    row.first_date,
                    row.last_date,
                    f"{row.total_gb_decimal:.2f}",
                ]
            )


def main() -> int:
    """Parse CLI args, run discovery search, and write ranked geometry options."""
    parser = argparse.ArgumentParser(
        description="Discover Sentinel-1 geometry candidates for a project AOI/time window."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory).",
    )
    parser.add_argument(
        "--config",
        default="",
        help="Optional stack TOML config path. Used for defaults when provided.",
    )
    parser.add_argument("--kml", default="", help="AOI KML path override.")
    parser.add_argument("--start", default="", help="Start datetime (ISO8601).")
    parser.add_argument("--end", default="", help="End datetime (ISO8601).")
    parser.add_argument("--platform", default="SENTINEL1", help="ASF platform enum name.")
    parser.add_argument(
        "--processing-level",
        default="SLC",
        help="ASF product type enum name.",
    )
    parser.add_argument("--beam-mode", default="IW", help="ASF beam mode enum name.")
    parser.add_argument(
        "--output-csv",
        default="",
        help="Output CSV path. Default: <outputs.root>/candidates/geometry_candidates.csv",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Output JSON summary path. Default: <outputs.root>/candidates/geometry_candidates.json",
    )
    parser.add_argument(
        "--min-unique-dates",
        type=int,
        default=5,
        help="Hide geometry groups with fewer than this many acquisition dates.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=20,
        help="Max rows printed to terminal (0 = all).",
    )
    args = parser.parse_args()

    try:
        import asf_search as asf  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        print(
            "Missing dependency: asf_search. Activate the project conda environment "
            "(for example: conda activate isce3-feb) before running discovery.",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(args.repo_root).resolve()
    cfg: dict[str, Any] = {}
    search_cfg: dict[str, Any] = {}
    output_root: Path | None = None

    if args.config:
        config_path = resolve_path(repo_root, args.config)
        cfg = read_toml(config_path)
        search_cfg = cfg.get("search", {})
        outputs_cfg = cfg.get("outputs", {})
        root_value = str(outputs_cfg.get("root", "")).strip()
        if root_value:
            output_root = resolve_path(repo_root, root_value)

    kml_value = args.kml or str(cfg.get("aoi", {}).get("kml", "")).strip()
    if not kml_value:
        print("Missing AOI KML. Pass --kml or provide aoi.kml in config.", file=sys.stderr)
        return 2
    kml_path = resolve_path(repo_root, kml_value)
    if not kml_path.exists():
        print(f"Missing KML file: {kml_path}", file=sys.stderr)
        return 2

    start = args.start or str(search_cfg.get("start", "")).strip()
    end = args.end or str(search_cfg.get("end", "")).strip()
    if not start or not end:
        print("Missing --start/--end (or search.start/search.end in config).", file=sys.stderr)
        return 2

    platform_name = args.platform or str(search_cfg.get("platform", "SENTINEL1"))
    processing_name = args.processing_level or str(search_cfg.get("processing_level", "SLC"))
    beam_name = args.beam_mode or str(search_cfg.get("beam_mode", "IW"))

    try:
        platform = enum_or_fail(asf.PLATFORM, platform_name, "platform")
        processing_level = enum_or_fail(asf.PRODUCT_TYPE, processing_name, "processing_level")
        beam_mode = enum_or_fail(asf.BEAMMODE, beam_name, "beam_mode")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    wkt = parse_kml_to_wkt(kml_path)
    results = asf.search(
        platform=[platform],
        processingLevel=[processing_level],
        beamMode=[beam_mode],
        start=start,
        end=end,
        intersectsWith=wkt,
    )

    result_list = sorted(results, key=lambda item: item.properties.get("startTime", ""))
    summaries = [s for s in build_summaries(result_list) if s.unique_dates >= args.min_unique_dates]

    if args.output_csv:
        output_csv = resolve_path(repo_root, args.output_csv)
    elif output_root is not None:
        output_csv = output_root / "candidates" / "geometry_candidates.csv"
    else:
        output_csv = None

    if args.output_json:
        output_json = resolve_path(repo_root, args.output_json)
    elif output_root is not None:
        output_json = output_root / "candidates" / "geometry_candidates.json"
    else:
        output_json = None

    print(f"AOI KML: {kml_path}")
    print(f"Search window: {start} -> {end}")
    print(f"Total matching scenes: {len(result_list)}")
    print(f"Geometry groups (after min-unique-dates={args.min_unique_dates}): {len(summaries)}")
    if summaries:
        print()
        print_table(summaries, max_rows=args.max_rows)
    else:
        print("No geometry groups met the minimum unique-date threshold.")

    if output_csv is not None:
        write_csv_rows(output_csv, summaries)
        print(f"\nWrote CSV: {output_csv}")

    if output_json is not None:
        payload = {
            "aoi_kml": str(kml_path),
            "start": start,
            "end": end,
            "platform": platform_name,
            "processing_level": processing_name,
            "beam_mode": beam_name,
            "total_matching_scenes": len(result_list),
            "min_unique_dates": args.min_unique_dates,
            "groups": [row.__dict__ for row in summaries],
        }
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote JSON: {output_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
