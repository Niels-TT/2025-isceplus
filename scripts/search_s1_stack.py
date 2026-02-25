#!/usr/bin/env python3
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
    with path.open("rb") as f:
        return tomllib.load(f)


def parse_kml_to_wkt(kml_path: Path) -> str:
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
    try:
        return getattr(enum_obj, value)
    except AttributeError as exc:
        raise ValueError(f"Invalid {field_name} value: {value}") from exc


def write_scene_csv(path: Path, results: Iterable) -> None:
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
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for item in results:
            props = item.properties
            writer.writerow({col: props.get(col, "") for col in columns})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search Sentinel-1 SLC scenes for an InSAR stack config."
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
    results = sorted(search_results, key=lambda item: item.properties.get("startTime", ""))

    unique_dates = sorted({item.properties["startTime"][:10] for item in results})
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

    geojson = {"type": "FeatureCollection", "features": []}
    if search_results:
        geojson = search_results.geojson()
    geojson_path.write_text(json.dumps(geojson, indent=2), encoding="utf-8")
    wkt_path.write_text(wkt + "\n", encoding="utf-8")

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
        "scene_count": len(results),
        "unique_date_count": len(unique_dates),
        "first_date": unique_dates[0] if unique_dates else None,
        "last_date": unique_dates[-1] if unique_dates else None,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    expected_scenes = int(search_cfg.get("expected_scenes", len(results)))
    expected_dates = int(search_cfg.get("expected_unique_dates", len(unique_dates)))
    scene_ok = len(results) == expected_scenes
    date_ok = len(unique_dates) == expected_dates

    print(f"Config: {config_path}")
    print(f"AOI: {kml_path}")
    print(f"Scenes: {len(results)} (expected {expected_scenes})")
    print(f"Unique dates: {len(unique_dates)} (expected {expected_dates})")
    print(f"Date span: {summary['first_date']} -> {summary['last_date']}")
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
