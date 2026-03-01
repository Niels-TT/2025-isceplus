#!/usr/bin/env python3
"""Search and materialize Sentinel-1 stack candidates from ASF.

Technical summary:
    Reads stack config, converts KML AOI to WKT, queries ASF with orbit/date
    constraints, and writes products (`scene_names.txt`, `scenes.csv`,
    `summary.json`, filtered GeoJSON, WKT).

Why:
    Freezes a reproducible scene list before large downloads and preprocessing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Iterable

import asf_search as asf

from stack_common import (
    DEFAULT_STACK_CONFIG_REL,
    buffered_kml_to_wkt,
    read_aoi_buffer_m,
    read_toml,
    resolve_path,
    resolve_stack_config,
)


def asf_enum(enum_obj, value: str, field_name: str):
    """Resolve string enum values against `asf_search` enums.

    Args:
        enum_obj: Enum container from `asf_search`.
        value: Enum name to resolve.
        field_name: Field label used in error messages.

    Returns:
        Enum value object.

    Raises:
        ValueError: If enum value name is invalid.
    """
    try:
        return getattr(enum_obj, value)
    except AttributeError as exc:
        raise ValueError(f"Invalid {field_name} value: {value}") from exc


def parse_optional_int(value: object, field_name: str, default: int = 0) -> int:
    """Parse optional integer config values with empty-as-default behavior."""
    text = "" if value is None else str(value).strip()
    if not text:
        return default
    try:
        parsed = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}: expected integer, got {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"Invalid {field_name}: must be >= 0, got {parsed}")
    return parsed


def write_scene_csv(path: Path, results: Iterable) -> None:
    """Write selected ASF scene properties to CSV.

    Args:
        path: Output CSV path.
        results: Iterable of ASF result objects.
    """
    columns = [
        "sceneName",
        "startTime",
        "stopTime",
        "platform",
        "beamModeType",
        "flightDirection",
        "pathNumber",
        "frameNumber",
        "orbit",
        "polarization",
        "processingLevel",
        "url",
        "bytes",
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for item in results:
            props = item.properties
            writer.writerow({col: props.get(col, "") for col in columns})


def filter_geojson(full_geojson: dict, selected_scene_names: set[str]) -> dict:
    """Filter a full GeoJSON feature collection to selected scenes only.

    Args:
        full_geojson: GeoJSON dict from ASF results.
        selected_scene_names: Scene names to retain.

    Returns:
        Filtered GeoJSON feature collection.
    """
    features = full_geojson.get("features", [])
    filtered = [
        f
        for f in features
        if f.get("properties", {}).get("sceneName") in selected_scene_names
    ]
    return {"type": "FeatureCollection", "features": filtered}


def main() -> int:
    """Parse CLI args, run ASF search, and write stack manifest products.

    Why:
        Lock a reproducible scene list before any large downloads or
        preprocessing/coregistration steps.

    Technical details:
        - Uses `asf_search.search` with platform/beam/orbit/date/AOI filters.
        - Applies configured date selection policy (e.g., first N from reference).
        - Writes deterministic search artifacts under `outputs.root`.
        - Optionally enforces expected scene/date counts.

    Returns:
        Exit code (0 for success).
    """
    parser = argparse.ArgumentParser(
        description="Search Sentinel-1 SLC scenes for a stack config."
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_STACK_CONFIG_REL,
        help="Path to stack TOML config (relative to repo root or absolute).",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory).",
    )
    parser.add_argument(
        "--allow-mismatch",
        action="store_true",
        help="Do not fail if expected scene/date counts differ.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    try:
        config_path = resolve_stack_config(repo_root, args.config)
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    cfg = read_toml(config_path)
    search_cfg = cfg["search"]
    aoi_cfg = cfg["aoi"]
    out_cfg = cfg["outputs"]

    kml_path = resolve_path(repo_root, aoi_cfg["kml"])
    aoi_buffer_m = read_aoi_buffer_m(cfg)
    wkt = buffered_kml_to_wkt(kml_path, aoi_buffer_m)

    platform = asf_enum(asf.PLATFORM, search_cfg["platform"], "platform")
    processing_level = asf_enum(
        asf.PRODUCT_TYPE, search_cfg["processing_level"], "processing_level"
    )
    beam_mode = asf_enum(asf.BEAMMODE, search_cfg["beam_mode"], "beam_mode")
    flight_direction = asf_enum(
        asf.FLIGHT_DIRECTION, search_cfg["flight_direction"], "flight_direction"
    )
    try:
        relative_orbit = parse_optional_int(search_cfg["relative_orbit"], "search.relative_orbit")
        frame_number = parse_optional_int(
            search_cfg.get("frame_number", 0),
            "search.frame_number",
            default=0,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    search_results = asf.search(
        platform=[platform],
        processingLevel=[processing_level],
        beamMode=[beam_mode],
        flightDirection=flight_direction,
        relativeOrbit=[relative_orbit],
        start=search_cfg["start"],
        end=search_cfg["end"],
        intersectsWith=wkt,
    )
    all_results_sorted = sorted(
        search_results, key=lambda item: item.properties.get("startTime", "")
    )
    if frame_number > 0:
        all_results_sorted = [
            item
            for item in all_results_sorted
            if str(item.properties.get("frameNumber", "")).strip().isdigit()
            and int(str(item.properties.get("frameNumber", "")).strip()) == frame_number
        ]
    full_unique_dates = sorted({item.properties["startTime"][:10] for item in all_results_sorted})

    results = all_results_sorted
    selected_dates = full_unique_dates
    scene_names = [item.properties.get("sceneName", "") for item in results]

    out_root = resolve_path(repo_root, out_cfg["root"])

    scene_list_path = out_root / out_cfg["scene_list"]
    scene_csv_path = out_root / out_cfg["metadata_csv"]
    summary_path = out_root / out_cfg["summary_json"]
    geojson_path = out_root / out_cfg["geojson"]
    wkt_path = out_root / out_cfg["wkt"]

    for p in [scene_list_path, scene_csv_path, summary_path, geojson_path, wkt_path]:
        p.parent.mkdir(parents=True, exist_ok=True)

    scene_list_path.write_text("\n".join(scene_names) + "\n", encoding="utf-8")
    write_scene_csv(scene_csv_path, results)

    full_geojson = {"type": "FeatureCollection", "features": []}
    if search_results:
        full_geojson = search_results.geojson()
    selected_set = set(scene_names)
    geojson = filter_geojson(full_geojson, selected_set)
    geojson_path.write_text(json.dumps(geojson, indent=2), encoding="utf-8")
    wkt_path.write_text(wkt + "\n", encoding="utf-8")

    selected_bytes = [
        int(item.properties.get("bytes", 0))
        for item in results
        if item.properties.get("bytes") is not None
    ]

    summary = {
        "stack_name": cfg["project"]["name"],
        "aoi_kml": str(kml_path),
        "aoi_buffer_m": aoi_buffer_m,
        "reference_date": search_cfg["reference_date"],
        "start": search_cfg["start"],
        "end": search_cfg["end"],
        "platform": search_cfg["platform"],
        "processing_level": search_cfg["processing_level"],
        "beam_mode": search_cfg["beam_mode"],
        "flight_direction": search_cfg["flight_direction"],
        "relative_orbit": relative_orbit,
        "frame_number": frame_number or None,
        "selection_mode": "all_from_start_end",
        "selected_scene_count": len(results),
        "selected_unique_date_count": len(selected_dates),
        "selected_first_date": selected_dates[0] if selected_dates else None,
        "selected_last_date": selected_dates[-1] if selected_dates else None,
        "selected_total_bytes": sum(selected_bytes),
        "selected_total_gb_decimal": round(sum(selected_bytes) / 1e9, 2),
        "full_scene_count": len(all_results_sorted),
        "full_unique_date_count": len(full_unique_dates),
        "full_first_date": full_unique_dates[0] if full_unique_dates else None,
        "full_last_date": full_unique_dates[-1] if full_unique_dates else None,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    expected_scenes = int(search_cfg.get("expected_scenes", 0))
    expected_dates = int(search_cfg.get("expected_unique_dates", 0))
    check_scene_count = expected_scenes > 0
    check_date_count = expected_dates > 0
    scene_ok = (not check_scene_count) or len(results) == expected_scenes
    date_ok = (not check_date_count) or len(selected_dates) == expected_dates

    expected_scene_label = str(expected_scenes) if check_scene_count else "disabled (0)"
    expected_date_label = str(expected_dates) if check_date_count else "disabled (0)"

    print(f"Config: {config_path}")
    print(f"AOI: {kml_path}")
    print(f"AOI search buffer: {aoi_buffer_m:.1f} m")
    print(
        "Date filter: start/end only "
        f"({search_cfg['start']} -> {search_cfg['end']}); reference_date is not used in search."
    )
    print(f"Geometry filter: {search_cfg['flight_direction']} orbit {relative_orbit}"
          + (f" frame {frame_number}" if frame_number > 0 else " (all frames)"))
    print(
        f"Selected scenes: {len(results)} (expected {expected_scene_label})"
        f" | Full matches: {len(all_results_sorted)}"
    )
    print(
        f"Selected dates: {len(selected_dates)} (expected {expected_date_label})"
        f" | Date span: {summary['selected_first_date']} -> {summary['selected_last_date']}"
    )
    print(f"Selected volume: {summary['selected_total_gb_decimal']} GB (decimal)")
    print(f"Scene list: {scene_list_path}")
    print(f"Metadata CSV: {scene_csv_path}")
    print(f"GeoJSON: {geojson_path}")
    print(f"Summary: {summary_path}")

    if (not scene_ok or not date_ok) and not args.allow_mismatch:
        print("Count mismatch detected. Use --allow-mismatch to continue anyway.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
