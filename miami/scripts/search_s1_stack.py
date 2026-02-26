#!/usr/bin/env python3
"""Search and materialize Sentinel-1 stack candidates from ASF.

Technical summary:
    Reads stack config, converts KML AOI to WKT, queries ASF with orbit/date
    constraints, applies local selection policy, and writes products
    (`scene_names.txt`, `scenes.csv`, `summary.json`, filtered GeoJSON, WKT).

Why:
    Freezes a reproducible scene list before large downloads and preprocessing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

import asf_search as asf

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


def read_toml(path: Path) -> dict:
    """Load a TOML file into a dictionary.

    Args:
        path: TOML file path.

    Returns:
        Parsed TOML content.
    """
    with path.open("rb") as f:
        return tomllib.load(f)


def parse_kml_to_wkt(kml_path: Path) -> str:
    """Convert a KML AOI polygon to WKT format for ASF queries.

    Args:
        kml_path: Path to AOI KML file.

    Returns:
        WKT POLYGON string.

    Raises:
        ValueError: If polygon coordinates are missing or invalid.
    """
    root = ET.parse(kml_path).getroot()
    coordinates = root.find(".//kml:coordinates", KML_NS)
    if coordinates is None or not coordinates.text:
        raise ValueError(f"No polygon coordinates found in KML: {kml_path}")

    points = []
    for token in coordinates.text.strip().split():
        lon, lat, *_ = token.split(",")
        points.append((float(lon), float(lat)))

    if len(points) < 4:
        raise ValueError(f"Invalid polygon in KML (too few points): {kml_path}")

    if points[0] != points[-1]:
        points.append(points[0])

    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in points) + "))"


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


def apply_selection_policy(results: list, selection_cfg: dict, reference_date: str) -> tuple[list, list[str]]:
    """Apply date-selection policy to full search results.

    Args:
        results: All ASF results sorted by start time.
        selection_cfg: Selection config from TOML.
        reference_date: Reference date in YYYY-MM-DD format.

    Returns:
        Tuple of (selected results, selected unique dates).

    Raises:
        ValueError: If selection mode or parameters are invalid.
    """
    mode = selection_cfg.get("mode", "none")
    max_dates = int(selection_cfg.get("max_dates", 0))
    require_reference = bool(selection_cfg.get("require_reference", True))

    if mode == "none":
        unique_dates = sorted({item.properties["startTime"][:10] for item in results})
        return results, unique_dates

    if mode != "first_n_from_reference":
        raise ValueError(f"Unsupported selection.mode: {mode}")
    if max_dates <= 0:
        raise ValueError("selection.max_dates must be > 0 for first_n_from_reference")

    all_dates = sorted({item.properties["startTime"][:10] for item in results})
    if require_reference and reference_date not in all_dates:
        raise ValueError(
            f"Reference date {reference_date} not found in search results. "
            "Choose another reference_date or broaden search constraints."
        )

    selected_dates = [d for d in all_dates if d >= reference_date][:max_dates]
    if len(selected_dates) < max_dates:
        print(
            f"Warning: requested {max_dates} dates from {reference_date}, "
            f"but only {len(selected_dates)} are available.",
            file=sys.stderr,
        )

    selected_set = set(selected_dates)
    selected_results = [
        item for item in results if item.properties["startTime"][:10] in selected_set
    ]

    return selected_results, selected_dates


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
        description="Search Sentinel-1 SLC scenes for the Miami stack config."
    )
    parser.add_argument(
        "--config",
        default="miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml",
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
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = repo_root / config_path
    config_path = config_path.resolve()

    cfg = read_toml(config_path)
    search_cfg = cfg["search"]
    selection_cfg = cfg.get("selection", {"mode": "none"})
    aoi_cfg = cfg["aoi"]
    out_cfg = cfg["outputs"]

    kml_path = Path(aoi_cfg["kml"])
    if not kml_path.is_absolute():
        kml_path = repo_root / kml_path
    kml_path = kml_path.resolve()
    wkt = parse_kml_to_wkt(kml_path)

    platform = asf_enum(asf.PLATFORM, search_cfg["platform"], "platform")
    processing_level = asf_enum(
        asf.PRODUCT_TYPE, search_cfg["processing_level"], "processing_level"
    )
    beam_mode = asf_enum(asf.BEAMMODE, search_cfg["beam_mode"], "beam_mode")
    flight_direction = asf_enum(
        asf.FLIGHT_DIRECTION, search_cfg["flight_direction"], "flight_direction"
    )
    relative_orbit = int(search_cfg["relative_orbit"])

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
    full_unique_dates = sorted({item.properties["startTime"][:10] for item in all_results_sorted})

    results, selected_dates = apply_selection_policy(
        all_results_sorted,
        selection_cfg,
        search_cfg["reference_date"],
    )
    scene_names = [item.properties.get("sceneName", "") for item in results]

    out_root = Path(out_cfg["root"])
    if not out_root.is_absolute():
        out_root = repo_root / out_root
    out_root = out_root.resolve()

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
        "reference_date": search_cfg["reference_date"],
        "start": search_cfg["start"],
        "end": search_cfg["end"],
        "platform": search_cfg["platform"],
        "processing_level": search_cfg["processing_level"],
        "beam_mode": search_cfg["beam_mode"],
        "flight_direction": search_cfg["flight_direction"],
        "relative_orbit": relative_orbit,
        "selection_mode": selection_cfg.get("mode", "none"),
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

    expected_scenes = int(search_cfg.get("expected_scenes", len(results)))
    expected_dates = int(search_cfg.get("expected_unique_dates", len(selected_dates)))
    scene_ok = len(results) == expected_scenes
    date_ok = len(selected_dates) == expected_dates

    print(f"Config: {config_path}")
    print(f"AOI: {kml_path}")
    print(
        f"Selected scenes: {len(results)} (expected {expected_scenes})"
        f" | Full matches: {len(all_results_sorted)}"
    )
    print(
        f"Selected dates: {len(selected_dates)} (expected {expected_dates})"
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
