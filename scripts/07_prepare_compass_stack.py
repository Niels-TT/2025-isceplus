#!/usr/bin/env python3
"""Prepare COMPASS run files for stack preprocessing and coregistration setup.

Technical summary:
    Validates scene completeness, DEM datum policy, and path/config integrity,
    then builds a deterministic `s1_geocode_stack.py` command and generates
    COMPASS run files plus a preparation summary.

Why:
    Catch input issues before expensive processing and keep execution
    reproducible across reruns.

Note:
    This stage prepares run files only; execution happens in
    `08_run_compass_runfiles.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml
from tqdm import tqdm

from stack_common import (
    DEFAULT_STACK_CONFIG_REL,
    buffered_kml_bbox,
    infer_stack_root,
    iso_to_yyyymmdd,
    read_aoi_buffer_m,
    read_scene_rows,
    read_toml,
    resolve_path,
    resolve_stack_config,
)

DEFAULT_BURST_DB_URL = (
    "https://github.com/opera-adt/burst_db/releases/download/v0.10.0/"
    "opera-burst-bbox-only.sqlite3"
)


def command_exists(cmd: str) -> bool:
    """Check whether a command is available on PATH.

    Args:
        cmd: Command name.

    Returns:
        True when command is found, otherwise False.
    """
    return shutil.which(cmd) is not None


def bool_cfg(cfg: dict, key: str, default: bool) -> bool:
    """Read a boolean-like config value with a default.

    Args:
        cfg: Configuration dictionary.
        key: Key to read.
        default: Fallback value when key is missing.

    Returns:
        Boolean interpretation of config value.
    """
    value = cfg.get(key, default)
    return bool(value)


def row_filename(row: dict[str, str]) -> str:
    """Resolve the local ZIP filename for a scene metadata row.

    Args:
        row: Scene metadata row from `scenes.csv`.

    Returns:
        ZIP filename inferred from URL or scene name.
    """
    scene_name = row.get("sceneName", "").strip()
    url = row.get("url", "").strip()
    filename = Path(urlparse(url).path).name
    if not filename:
        filename = f"{scene_name}.zip"
    return filename


def find_scene_issues(
    scene_rows: list[dict[str, str]], slc_dir: Path
) -> tuple[list[str], list[tuple[str, int, int]]]:
    """Find missing scenes and byte-size mismatches in local SLC storage.

    Args:
        scene_rows: Rows loaded from scene metadata CSV.
        slc_dir: Directory containing downloaded SLC ZIP files.

    Returns:
        Tuple of:
        - list of missing scene labels
        - list of (scene_label, expected_bytes, got_bytes) mismatches
    """
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
    """Validate DEM vertical datum policy from configuration.

    Args:
        dem_cfg: DEM config dictionary from stack TOML.

    Returns:
        Tuple of (is_valid, vertical_datum, require_ellipsoid_heights).
    """
    vertical_datum = str(dem_cfg.get("vertical_datum", "")).strip().upper()
    require_ellipsoid = bool_cfg(dem_cfg, "require_ellipsoid_heights", True)
    if not vertical_datum:
        return False, "UNSET", require_ellipsoid
    if require_ellipsoid and vertical_datum != "WGS84_ELLIPSOID":
        return False, vertical_datum, require_ellipsoid
    return True, vertical_datum, require_ellipsoid


def parse_yyyymmdd(value: str) -> date:
    """Parse a `YYYYMMDD` string to a `date`.

    Args:
        value: Date string in `YYYYMMDD` format.

    Returns:
        Parsed date object.
    """
    return datetime.strptime(value, "%Y%m%d").date()


def summarize_orbit_cache(orbit_root: Path) -> dict[str, int]:
    """Summarize local orbit cache content by orbit type.

    Args:
        orbit_root: Orbit cache directory.

    Returns:
        Counts for total/orbit-type files.
    """
    if not orbit_root.exists():
        return {
            "total_files": 0,
            "poeorb_files": 0,
            "resorb_files": 0,
            "other_files": 0,
        }

    files = [p for p in orbit_root.rglob("*") if p.is_file()]
    poeorb_count = sum("POEORB" in p.name.upper() for p in files)
    resorb_count = sum("RESORB" in p.name.upper() for p in files)
    return {
        "total_files": len(files),
        "poeorb_files": poeorb_count,
        "resorb_files": resorb_count,
        "other_files": len(files) - poeorb_count - resorb_count,
    }


def normalize_polarization_value(value: object) -> tuple[object, str | None]:
    """Normalize runconfig polarization value for schema compatibility.

    Args:
        value: Raw value from `runconfig.groups.processing.polarization`.

    Returns:
        Tuple of normalized value and optional note.
    """
    if not isinstance(value, list):
        return value, None

    values = [str(v).strip() for v in value if str(v).strip()]
    if not values:
        return "co-pol", "empty polarization list normalized to co-pol"
    if len(values) == 1:
        return values[0], "single-item polarization list normalized to scalar"

    unique = set(values)
    if unique == {"co-pol", "cross-pol"}:
        return "dual-pol", "co-pol/cross-pol list normalized to dual-pol"
    return values[0], f"multi-item polarization list normalized to first item ({values[0]})"


def validate_runconfig_schema(data: object, runconfig: Path) -> tuple[bool, str]:
    """Validate expected runconfig schema before applying compatibility patches.

    Args:
        data: Parsed runconfig YAML object.
        runconfig: Source runconfig path.

    Returns:
        Tuple of (is_valid, message).
    """
    if not isinstance(data, dict):
        return False, f"{runconfig.name}: top-level YAML is not a mapping"
    rc = data.get("runconfig")
    if not isinstance(rc, dict):
        return False, f"{runconfig.name}: missing 'runconfig' mapping"
    groups = rc.get("groups")
    if not isinstance(groups, dict):
        return False, f"{runconfig.name}: missing 'runconfig.groups' mapping"
    processing = groups.get("processing")
    if not isinstance(processing, dict):
        return False, f"{runconfig.name}: missing 'runconfig.groups.processing' mapping"
    if "polarization" not in processing:
        return False, f"{runconfig.name}: missing 'runconfig.groups.processing.polarization'"
    quality = groups.get("quality_assurance")
    if quality is not None and not isinstance(quality, dict):
        return False, f"{runconfig.name}: 'runconfig.groups.quality_assurance' is not a mapping"
    browse = (quality or {}).get("browse_image")
    if browse is not None and not isinstance(browse, dict):
        return False, f"{runconfig.name}: 'runconfig.groups.quality_assurance.browse_image' is not a mapping"
    return True, "ok"


def normalize_runconfigs(
    runconfigs_dir: Path, browse_image_enabled: bool
) -> tuple[int, int, list[str], list[str]]:
    """Patch generated runconfigs to match installed COMPASS behavior.

    Why:
        Some COMPASS builds write polarization as a YAML list, while
        `s1_cslc_geo` schema expects a scalar enum.
        On some environments (including WSL), browse-image generation can fail
        due HDF5/GDAL file-access interactions, so it is controlled explicitly.

    Args:
        runconfigs_dir: Directory containing `geo_runconfig_*.yaml` files.
        browse_image_enabled: Whether runconfigs should enable browse output.

    Returns:
        Tuple of:
        - number of runconfig files changed
        - number of files where browse-image setting was updated
        - compatibility notes
        - schema validation error messages
    """
    changed = 0
    browse_changed = 0
    notes: list[str] = []
    schema_errors: list[str] = []
    for runconfig in sorted(runconfigs_dir.glob("geo_runconfig_*.yaml")):
        data = yaml.safe_load(runconfig.read_text(encoding="utf-8"))
        schema_ok, schema_msg = validate_runconfig_schema(data, runconfig)
        if not schema_ok:
            schema_errors.append(schema_msg)
            continue
        file_changed = False
        processing = (
            data.get("runconfig", {})
            .get("groups", {})
            .get("processing", {})
        )
        current = processing.get("polarization")
        normalized, note = normalize_polarization_value(current)
        if normalized != current:
            processing["polarization"] = normalized
            file_changed = True
        groups = data.setdefault("runconfig", {}).setdefault("groups", {})
        quality = groups.setdefault("quality_assurance", {})
        browse = quality.setdefault("browse_image", {})
        browse_current = bool(browse.get("enabled", True))
        if browse_current != browse_image_enabled:
            browse["enabled"] = browse_image_enabled
            browse_changed += 1
            file_changed = True

        if not file_changed:
            continue
        runconfig.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        changed += 1
        if note:
            notes.append(f"{runconfig.name}: {note}")
    return changed, browse_changed, notes, schema_errors


def stream_download(url: str, out_path: Path, timeout_s: int) -> int:
    """Stream a file download to disk with progress feedback.

    Args:
        url: Source URL.
        out_path: Destination file path.
        timeout_s: Request timeout in seconds.

    Returns:
        Number of bytes written to output file.

    Raises:
        RuntimeError: If HTTP request fails.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")
    if tmp_path.exists():
        tmp_path.unlink()

    with requests.get(url, stream=True, timeout=timeout_s) as r:
        try:
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            body = ""
            try:
                body = r.text[:500] if r.text else ""
            except Exception:  # noqa: BLE001
                body = ""
            raise RuntimeError(
                f"Burst DB download failed (HTTP {r.status_code}): {body}"
            ) from exc

        total_bytes = None
        content_len = r.headers.get("Content-Length") or r.headers.get("content-length")
        if content_len and content_len.isdigit():
            total_bytes = int(content_len)

        written = 0
        with (
            tmp_path.open("wb") as f,
            tqdm(
                total=total_bytes,
                desc="Burst DB download",
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                dynamic_ncols=True,
            ) as bar,
        ):
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                n = len(chunk)
                written += n
                bar.update(n)

    os.replace(tmp_path, out_path)
    return written


def main() -> int:
    """Parse CLI args, validate preprocessing inputs, and generate run files.

    Why:
        Catch incomplete SLCs, DEM datum issues, and config errors before the
        expensive coregistration stage.

    Technical details:
        - Validates local ZIP presence and exact byte size against `scenes.csv`.
        - Enforces configured DEM vertical datum policy.
        - Derives AOI bbox and date window for `s1_geocode_stack.py`.
        - Generates COMPASS run files and writes `prepare_summary.json`.

    Returns:
        Exit code (0 for success).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Prepare COMPASS run files for stack coregistration via s1_geocode_stack.py. "
            "Orbit download is integrated through COMPASS/S1Reader."
        )
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_STACK_CONFIG_REL,
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
        help="DEM path override (otherwise read from ancillary.dem.file in processing_configuration.toml).",
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
    parser.add_argument(
        "--burst-db-url",
        default="",
        help=(
            "Optional OPERA burst DB URL override. "
            "Defaults to processing.compass.burst_db_url or built-in release URL."
        ),
    )
    parser.add_argument(
        "--burst-db-timeout-seconds",
        type=int,
        default=300,
        help="HTTP timeout for auto-downloading burst DB when missing.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    try:
        config_path = resolve_stack_config(repo_root, args.config)
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    cfg = read_toml(config_path)
    stack_root = infer_stack_root(config_path)

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
            "No DEM path configured. Set ancillary.dem.file in processing_configuration.toml or pass --dem-file.",
            file=sys.stderr,
        )
        return 2
    dem_file = resolve_path(repo_root, dem_value)

    work_dir = resolve_path(
        repo_root,
        compass_cfg.get("work_dir", str(stack_root / "stack" / "compass")),
    )
    orbit_dir_value = compass_cfg.get("orbit_dir", "")
    orbit_dir = resolve_path(repo_root, orbit_dir_value) if orbit_dir_value else None
    burst_db_value = str(compass_cfg.get("burst_db_file", "")).strip()
    burst_db_file = resolve_path(repo_root, burst_db_value) if burst_db_value else None
    burst_db_url_cfg = str(compass_cfg.get("burst_db_url", "")).strip()
    burst_db_url = args.burst_db_url or burst_db_url_cfg or DEFAULT_BURST_DB_URL
    burst_db_downloaded = False
    burst_db_bytes: int | None = None

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
        print("Download DEM first with scripts/06_download_dem_opentopography.py.", file=sys.stderr)
        return 2
    if not command_exists("s1_geocode_stack.py"):
        print("Missing command: s1_geocode_stack.py. Install COMPASS in isce3-feb.", file=sys.stderr)
        return 2
    if burst_db_file is not None:
        if burst_db_file.exists() and burst_db_file.stat().st_size == 0:
            print(f"Burst DB file exists but is empty, re-downloading: {burst_db_file}")
            burst_db_file.unlink()
        if not burst_db_file.exists():
            if args.dry_run:
                print(f"Burst DB file missing (dry-run): {burst_db_file}")
                print(f"Burst DB URL (would download): {burst_db_url}")
            else:
                print(f"Missing burst DB file: {burst_db_file}")
                print(f"Downloading OPERA burst DB from: {burst_db_url}")
                try:
                    burst_db_bytes = stream_download(
                        burst_db_url,
                        burst_db_file,
                        timeout_s=args.burst_db_timeout_seconds,
                    )
                except RuntimeError as exc:
                    print(str(exc), file=sys.stderr)
                    print(
                        "Set processing.compass.burst_db_url or use --burst-db-url to provide a reachable source.",
                        file=sys.stderr,
                    )
                    return 2
                burst_db_downloaded = True
                print(f"Downloaded burst DB bytes: {burst_db_bytes}")
        if burst_db_file.exists():
            burst_db_bytes = burst_db_file.stat().st_size

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
            "Set an explicit vertical datum in processing_configuration.toml for reproducible geometry handling.",
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
    aoi_buffer_m = read_aoi_buffer_m(cfg)
    west, south, east, north = buffered_kml_bbox(kml_path, aoi_buffer_m)

    csv_dates = sorted(
        iso_to_yyyymmdd(row["startTime"])
        for row in scene_rows
        if row.get("startTime")
    )
    start_date = args.start_date or (csv_dates[0] if csv_dates else iso_to_yyyymmdd(search_cfg["start"]))
    end_date_inclusive = args.end_date or (
        csv_dates[-1] if csv_dates else iso_to_yyyymmdd(search_cfg["end"])
    )
    try:
        start_day = parse_yyyymmdd(start_date)
        end_day_inclusive = parse_yyyymmdd(end_date_inclusive)
    except ValueError:
        print(
            "Invalid date override. Use YYYYMMDD for --start-date/--end-date.",
            file=sys.stderr,
        )
        return 2
    if end_day_inclusive < start_day:
        print(
            f"Invalid date range: end date {end_date_inclusive} is before start date {start_date}.",
            file=sys.stderr,
        )
        return 2
    # COMPASS treats -ed as an exclusive upper bound, so include the last
    # acquisition day by passing end_date + 1 day.
    end_date_exclusive = (end_day_inclusive + timedelta(days=1)).strftime("%Y%m%d")

    pol = compass_cfg.get("pol", "co-pol")
    x_spac = float(compass_cfg.get("x_spacing_m", 5.0))
    y_spac = float(compass_cfg.get("y_spacing_m", 10.0))
    common_bursts_only = bool_cfg(compass_cfg, "common_bursts_only", True)
    browse_image_enabled = bool_cfg(compass_cfg, "browse_image", False)
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
        end_date_exclusive,
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
    if burst_db_file is not None:
        cmd += ["--burst-db-file", str(burst_db_file)]
    if common_bursts_only:
        cmd.append("--common-bursts-only")
    if output_epsg is not None:
        cmd += ["-e", str(int(output_epsg))]
    if not flatten:
        cmd.append("-nf")
    if not corrections:
        cmd.append("-nc")

    effective_orbit_dir = orbit_dir if orbit_dir is not None else (work_dir / "orbits")

    print(f"Config: {config_path}")
    print(f"Scene CSV: {scene_csv}")
    print(f"SLC dir: {slc_dir}")
    print(f"DEM file: {dem_file}")
    if not dem_exists:
        print("DEM exists: no (dry-run warning)")
    print(f"DEM vertical datum: {vertical_datum}")
    print(f"Require ellipsoid heights: {require_ellipsoid}")
    print(f"Work dir: {work_dir}")
    print(f"Orbit dir: {effective_orbit_dir}")
    print(f"Burst DB file: {burst_db_file or '(COMPASS default)'}")
    print(f"Burst DB URL: {burst_db_url if burst_db_file else '(not used)'}")
    print(f"Burst DB downloaded in this run: {burst_db_downloaded}")
    print(f"AOI processing buffer: {aoi_buffer_m:.1f} m")
    print(f"Browse image enabled: {browse_image_enabled}")
    print("Orbit retrieval: COMPASS/S1Reader uses local cache and auto-downloads missing files.")
    print("Orbit preference: POEORB (precise) first; RESORB only as fallback.")
    print(f"Date range (requested inclusive): {start_date} -> {end_date_inclusive}")
    print(f"Date range (passed to COMPASS; exclusive end): {start_date} -> {end_date_exclusive}")
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
    runconfig_dir = work_dir / "runconfigs"
    (
        runconfig_fix_count,
        browse_fix_count,
        pol_fix_notes,
        schema_errors,
    ) = normalize_runconfigs(
        runconfig_dir, browse_image_enabled=browse_image_enabled
    )
    if schema_errors:
        print(
            "Runconfig schema check failed for generated COMPASS runconfigs. "
            "This usually means an upstream COMPASS schema change.",
            file=sys.stderr,
        )
        for msg in schema_errors:
            print(f"  - {msg}", file=sys.stderr)
        print(
            "Update normalize_runconfigs compatibility logic before continuing.",
            file=sys.stderr,
        )
        return 5
    orbit_summary = summarize_orbit_cache(effective_orbit_dir)
    summary = {
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "scene_csv": str(scene_csv),
        "scene_count_csv": len(scene_rows),
        "missing_scene_count": len(missing_scenes),
        "size_mismatch_scene_count": len(size_mismatch),
        "work_dir": str(work_dir),
        "orbit_dir": str(effective_orbit_dir),
        "burst_db_file": str(burst_db_file) if burst_db_file else None,
        "burst_db_url": burst_db_url if burst_db_file else None,
        "burst_db_downloaded": burst_db_downloaded,
        "burst_db_bytes": burst_db_bytes,
        "dem_file": str(dem_file),
        "dem_vertical_datum": vertical_datum,
        "require_ellipsoid_heights": require_ellipsoid,
        "command": cmd,
        "date_start": start_date,
        "date_end_inclusive": end_date_inclusive,
        "date_end_exclusive_for_compass": end_date_exclusive,
        "aoi_buffer_m": aoi_buffer_m,
        "bbox_wsen": [west, south, east, north],
        "run_file_count": len(run_files),
        "runconfig_fix_count": runconfig_fix_count,
        "runconfig_browse_image_fix_count": browse_fix_count,
        "runconfig_polarization_fix_count": len(pol_fix_notes),
        "runconfig_polarization_fix_notes": pol_fix_notes,
        "runconfig_schema_error_count": len(schema_errors),
        "runconfig_schema_errors": schema_errors,
        "orbit_cache_summary": orbit_summary,
    }
    summary_path = work_dir / "prepare_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nPrepared run files: {len(run_files)}")
    print(
        "Runconfig compatibility fixes: "
        f"{runconfig_fix_count} files updated "
        f"(polarization notes: {len(pol_fix_notes)}, "
        f"browse-image setting updates: {browse_fix_count})."
    )
    print(
        "Orbit cache summary: "
        f"{orbit_summary['poeorb_files']} POEORB, "
        f"{orbit_summary['resorb_files']} RESORB, "
        f"{orbit_summary['other_files']} other files "
        f"(total {orbit_summary['total_files']})."
    )
    if orbit_summary["poeorb_files"] > 0 and orbit_summary["resorb_files"] == 0:
        print("Orbit status: precise POEORB files are being used.")
    elif orbit_summary["resorb_files"] > 0 and orbit_summary["poeorb_files"] == 0:
        print("Orbit status: RESORB-only fallback currently in use.", file=sys.stderr)
    elif orbit_summary["resorb_files"] > 0 and orbit_summary["poeorb_files"] > 0:
        print("Orbit status: mixed POEORB/RESORB cache.", file=sys.stderr)
    else:
        print("Orbit status: no local orbit files detected in cache.", file=sys.stderr)
    print(f"Summary: {summary_path}")
    print("Next: run scripts/08_run_compass_runfiles.py to execute coregistration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
