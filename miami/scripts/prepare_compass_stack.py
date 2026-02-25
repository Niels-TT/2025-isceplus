#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from stack_common import (
    buffer_bbox,
    iso_to_yyyymmdd,
    kml_bbox,
    read_scene_rows,
    read_toml,
    resolve_path,
)


def command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def bool_cfg(cfg: dict, key: str, default: bool) -> bool:
    value = cfg.get(key, default)
    return bool(value)


def row_filename(row: dict[str, str]) -> str:
    scene_name = row.get("sceneName", "").strip()
    url = row.get("url", "").strip()
    filename = Path(urlparse(url).path).name
    if not filename:
        filename = f"{scene_name}.zip"
    return filename


def find_scene_issues(
    scene_rows: list[dict[str, str]], slc_dir: Path
) -> tuple[list[str], list[tuple[str, int, int]]]:
    missing: list[str] = []
    size_mismatch: list[tuple[str, int, int]] = []

    for row in scene_rows:
        scene_name = row.get("sceneName", "").strip()
        scene_label = scene_name or row_filename(row)
        scene_path = slc_dir / row_filename(row)
        if not scene_path.exists():
            missing.append(scene_label)
            continue

        bytes_str = row.get("bytes", "").strip()
        expected_bytes = int(bytes_str) if bytes_str.isdigit() else None
        if expected_bytes is None:
            continue

        got_bytes = scene_path.stat().st_size
        if got_bytes != expected_bytes:
            size_mismatch.append((scene_label, expected_bytes, got_bytes))

    return missing, size_mismatch


def validate_vertical_datum(dem_cfg: dict) -> tuple[bool, str, bool]:
    vertical_datum = str(dem_cfg.get("vertical_datum", "")).strip().upper()
    require_ellipsoid = bool_cfg(dem_cfg, "require_ellipsoid_heights", True)
    if not vertical_datum:
        return False, "UNSET", require_ellipsoid
    if require_ellipsoid and vertical_datum != "WGS84_ELLIPSOID":
        return False, vertical_datum, require_ellipsoid
    return True, vertical_datum, require_ellipsoid


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare COMPASS run files for stack coregistration via s1_geocode_stack.py. "
            "Orbit download is integrated through COMPASS/S1Reader."
        )
    )
    parser.add_argument(
        "--config",
        default="miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml",
        help="Path to stack TOML config.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print checks and the exact COMPASS command without running it.",
    )
    parser.add_argument(
        "--allow-missing-scenes",
        action="store_true",
        help="Continue even if scenes are missing or size-mismatched in stack/slc.",
    )
    parser.add_argument(
        "--dem-file",
        default="",
        help="DEM path override (otherwise read from ancillary.dem.file in stack.toml).",
    )
    parser.add_argument(
        "--start-date",
        default="",
        help="Optional start date override (YYYYMMDD).",
    )
    parser.add_argument(
        "--end-date",
        default="",
        help="Optional end date override (YYYYMMDD).",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    config_path = resolve_path(repo_root, args.config)
    cfg = read_toml(config_path)

    out_cfg = cfg["outputs"]
    storage_cfg = cfg["storage"]
    search_cfg = cfg["search"]
    compass_cfg = cfg.get("processing", {}).get("compass", {})
    dem_cfg = cfg.get("ancillary", {}).get("dem", {})

    out_root = resolve_path(repo_root, out_cfg["root"])
    scene_csv = out_root / out_cfg["metadata_csv"]
    slc_dir = resolve_path(repo_root, storage_cfg["slc_dir"])

    dem_value = args.dem_file or dem_cfg.get("file", "")
    if not dem_value:
        print(
            "No DEM path configured. Set ancillary.dem.file in stack.toml or pass --dem-file.",
            file=sys.stderr,
        )
        return 2
    dem_file = resolve_path(repo_root, dem_value)

    work_dir = resolve_path(
        repo_root,
        compass_cfg.get("work_dir", "miami/insar/us_isleofnormandy_s1_asc_t48/stack/compass"),
    )
    orbit_dir_value = compass_cfg.get("orbit_dir", "")
    orbit_dir = resolve_path(repo_root, orbit_dir_value) if orbit_dir_value else None

    if not scene_csv.exists():
        print(f"Missing scene metadata CSV: {scene_csv}", file=sys.stderr)
        print("Run stack search first.", file=sys.stderr)
        return 2
    if not slc_dir.exists():
        print(f"Missing SLC directory: {slc_dir}", file=sys.stderr)
        return 2
    dem_exists = dem_file.exists()
    if not dem_exists and not args.dry_run:
        print(f"Missing DEM file: {dem_file}", file=sys.stderr)
        print("Download DEM first with miami/scripts/download_dem_opentopography.py.", file=sys.stderr)
        return 2
    if not command_exists("s1_geocode_stack.py"):
        print("Missing command: s1_geocode_stack.py. Install COMPASS in isce3-feb.", file=sys.stderr)
        return 2

    vd_ok, vertical_datum, require_ellipsoid = validate_vertical_datum(dem_cfg)
    if not vd_ok:
        if vertical_datum == "UNSET":
            print(
                "Vertical datum check failed: ancillary.dem.vertical_datum is not set.",
                file=sys.stderr,
            )
        else:
            print(
                "Vertical datum check failed: "
                f"configured DEM datum is '{vertical_datum}', but "
                "require_ellipsoid_heights=true expects 'WGS84_ELLIPSOID'.",
                file=sys.stderr,
            )
        print(
            "Set an explicit vertical datum in stack.toml for reproducible geometry handling.",
            file=sys.stderr,
        )
        return 2

    scene_rows = read_scene_rows(scene_csv)
    if not scene_rows:
        print(f"No rows found in scene CSV: {scene_csv}", file=sys.stderr)
        return 2

    missing_scenes, size_mismatch = find_scene_issues(scene_rows, slc_dir)
    if (missing_scenes or size_mismatch) and not args.allow_missing_scenes and not args.dry_run:
        print(
            f"Scene completeness check failed in {slc_dir}.",
            file=sys.stderr,
        )
        if missing_scenes:
            print(f"Missing scenes: {len(missing_scenes)}", file=sys.stderr)
            print("First missing entries:", file=sys.stderr)
            for name in missing_scenes[:10]:
                print(f"  - {name}", file=sys.stderr)
        if size_mismatch:
            print(f"Size-mismatch scenes: {len(size_mismatch)}", file=sys.stderr)
            print("First size mismatches:", file=sys.stderr)
            for name, expected, got in size_mismatch[:10]:
                print(f"  - {name}: expected={expected} got={got}", file=sys.stderr)
        print(
            "Wait for download completion or pass --allow-missing-scenes.",
            file=sys.stderr,
        )
        return 3

    kml_path = resolve_path(repo_root, cfg["aoi"]["kml"])
    bbox_buffer_deg = float(compass_cfg.get("bbox_buffer_deg", 0.02))
    west, south, east, north = buffer_bbox(kml_bbox(kml_path), bbox_buffer_deg)

    csv_dates = sorted(
        iso_to_yyyymmdd(row["startTime"])
        for row in scene_rows
        if row.get("startTime")
    )
    start_date = args.start_date or (csv_dates[0] if csv_dates else iso_to_yyyymmdd(search_cfg["start"]))
    end_date = args.end_date or (csv_dates[-1] if csv_dates else iso_to_yyyymmdd(search_cfg["end"]))

    pol = compass_cfg.get("pol", "co-pol")
    x_spac = float(compass_cfg.get("x_spacing_m", 5.0))
    y_spac = float(compass_cfg.get("y_spacing_m", 10.0))
    common_bursts_only = bool_cfg(compass_cfg, "common_bursts_only", True)
    flatten = bool_cfg(compass_cfg, "flatten", True)
    corrections = bool_cfg(compass_cfg, "corrections", True)
    output_epsg = compass_cfg.get("output_epsg")

    cmd = [
        "s1_geocode_stack.py",
        "-s",
        str(slc_dir),
        "-d",
        str(dem_file),
        "-w",
        str(work_dir),
        "-sd",
        start_date,
        "-ed",
        end_date,
        "-p",
        pol,
        "-dx",
        f"{x_spac:g}",
        "-dy",
        f"{y_spac:g}",
        "--bbox",
        f"{west:.8f}",
        f"{south:.8f}",
        f"{east:.8f}",
        f"{north:.8f}",
        "--bbox-epsg",
        "4326",
    ]
    if orbit_dir is not None:
        cmd += ["-o", str(orbit_dir)]
    if common_bursts_only:
        cmd.append("--common-bursts-only")
    if output_epsg is not None:
        cmd += ["-e", str(int(output_epsg))]
    if not flatten:
        cmd.append("-nf")
    if not corrections:
        cmd.append("-nc")

    print(f"Config: {config_path}")
    print(f"Scene CSV: {scene_csv}")
    print(f"SLC dir: {slc_dir}")
    print(f"DEM file: {dem_file}")
    if not dem_exists:
        print("DEM exists: no (dry-run warning)")
    print(f"DEM vertical datum: {vertical_datum}")
    print(f"Require ellipsoid heights: {require_ellipsoid}")
    print(f"Work dir: {work_dir}")
    print(f"Orbit dir: {orbit_dir or '(auto under work_dir/orbits)'}")
    print(f"Date range: {start_date} -> {end_date}")
    print(f"BBox (W,S,E,N): {west:.6f}, {south:.6f}, {east:.6f}, {north:.6f}")
    print(f"Scene rows in CSV: {len(scene_rows)}")
    print(f"Missing scenes on disk: {len(missing_scenes)}")
    print(f"Size-mismatch scenes: {len(size_mismatch)}")
    print("\nCOMPASS command:")
    print(" ".join(cmd))

    if args.dry_run:
        return 0

    work_dir.mkdir(parents=True, exist_ok=True)
    if orbit_dir is not None:
        orbit_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(cmd, check=True)

    run_files = sorted((work_dir / "run_files").glob("run_*.sh"))
    summary = {
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "scene_csv": str(scene_csv),
        "scene_count_csv": len(scene_rows),
        "missing_scene_count": len(missing_scenes),
        "size_mismatch_scene_count": len(size_mismatch),
        "work_dir": str(work_dir),
        "orbit_dir": str(orbit_dir) if orbit_dir else None,
        "dem_file": str(dem_file),
        "dem_vertical_datum": vertical_datum,
        "require_ellipsoid_heights": require_ellipsoid,
        "command": cmd,
        "date_start": start_date,
        "date_end": end_date,
        "bbox_wsen": [west, south, east, north],
        "run_file_count": len(run_files),
    }
    summary_path = work_dir / "prepare_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nPrepared run files: {len(run_files)}")
    print(f"Summary: {summary_path}")
    print("Next: run miami/scripts/run_compass_runfiles.py to execute coregistration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
