#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from tqdm import tqdm

from stack_common import buffer_bbox, kml_bbox, read_toml, resolve_path

OPENTOPO_URL = "https://portal.opentopography.org/API/globaldem"


def read_topoapi_key() -> str:
    key_path = Path.home() / ".topoapi"
    if not key_path.exists():
        raise RuntimeError(
            "Missing ~/.topoapi. Add your OpenTopography API key there (chmod 600 ~/.topoapi)."
        )
    key = key_path.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError("~/.topoapi is empty.")
    return key


def stream_download(url: str, params: dict[str, str], out_path: Path, timeout_s: int) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")
    if tmp_path.exists():
        tmp_path.unlink()

    with requests.get(url, params=params, stream=True, timeout=timeout_s) as r:
        try:
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            body = r.text[:500] if r.text else ""
            raise RuntimeError(f"OpenTopography HTTP {r.status_code}: {body}") from e

        total_bytes = None
        content_len = r.headers.get("Content-Length") or r.headers.get("content-length")
        if content_len and content_len.isdigit():
            total_bytes = int(content_len)

        written = 0
        with (
            tmp_path.open("wb") as f,
            tqdm(
                total=total_bytes,
                desc="DEM download",
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
    parser = argparse.ArgumentParser(
        description="Download a DEM from OpenTopography for the stack AOI."
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
        "--demtype",
        default="",
        help="OpenTopography DEM type (e.g. SRTM_GL1_Ellip, COP30). Defaults to stack config.",
    )
    parser.add_argument(
        "--buffer-deg",
        type=float,
        default=-1.0,
        help="AOI bbox buffer in degrees. Defaults to config or 0.02.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output DEM path override.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing DEM file.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="HTTP timeout in seconds.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    config_path = resolve_path(repo_root, args.config)
    cfg = read_toml(config_path)
    dem_cfg = cfg.get("ancillary", {}).get("dem", {})

    aoi_kml = resolve_path(repo_root, cfg["aoi"]["kml"])
    demtype = args.demtype or dem_cfg.get("demtype", "COP30")
    buffer_deg = args.buffer_deg if args.buffer_deg >= 0 else float(dem_cfg.get("bbox_buffer_deg", 0.02))
    vertical_datum = str(dem_cfg.get("vertical_datum", "")).strip() or "UNSET"

    output_value = args.output or dem_cfg.get("file")
    if not output_value:
        print(
            "No DEM output path configured. Set ancillary.dem.file in stack.toml or pass --output.",
            file=sys.stderr,
        )
        return 2
    dem_path = resolve_path(repo_root, output_value)

    if dem_path.exists() and not args.overwrite:
        print(f"DEM already exists: {dem_path}")
        print("Use --overwrite to re-download.")
        return 0

    xmin, ymin, xmax, ymax = kml_bbox(aoi_kml)
    west, south, east, north = buffer_bbox((xmin, ymin, xmax, ymax), buffer_deg)
    api_key = read_topoapi_key()

    params: dict[str, str] = {
        "demtype": demtype,
        "south": f"{south:.8f}",
        "north": f"{north:.8f}",
        "west": f"{west:.8f}",
        "east": f"{east:.8f}",
        "outputFormat": "GTiff",
        "API_Key": api_key,
    }

    print(f"Config: {config_path}")
    print(f"AOI KML: {aoi_kml}")
    print(f"DEM output: {dem_path}")
    print(f"DEM type: {demtype}")
    print(f"DEM vertical datum (config): {vertical_datum}")
    print(f"Buffered bbox (W,S,E,N): {west:.6f}, {south:.6f}, {east:.6f}, {north:.6f}")

    written = stream_download(OPENTOPO_URL, params, dem_path, timeout_s=args.timeout_seconds)

    meta = {
        "download_utc": datetime.now(timezone.utc).isoformat(),
        "source_url": OPENTOPO_URL,
        "request": {
            "demtype": demtype,
            "south": south,
            "north": north,
            "west": west,
            "east": east,
            "outputFormat": "GTiff",
        },
        "output_file": str(dem_path),
        "vertical_datum_config": vertical_datum,
        "bytes_written": written,
    }
    meta_path = dem_path.with_suffix(dem_path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Downloaded DEM bytes: {written}")
    print(f"DEM metadata: {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
