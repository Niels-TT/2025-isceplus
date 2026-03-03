#!/usr/bin/env python3
"""Download selected raw Sentinel-1 SLC ZIPs from ASF.

Technical summary:
    Reads selected scenes from `scenes.csv`, authenticates with Earthdata
    credentials from `~/.netrc`, streams each ZIP to `stack/slc`, updates
    `download_manifest.json`, and enforces disk-space guardrails.

Why:
    Stage raw inputs safely and reproducibly before DEM/orbit-aware
    preprocessing and coregistration.
"""

from __future__ import annotations

import argparse
import csv
import json
import netrc
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import asf_search as asf
from tqdm import tqdm

from stack_common import (
    DEFAULT_STACK_CONFIG_REL,
    parse_kml_points,
    read_toml,
    resolve_path,
    resolve_stack_config,
)


@dataclass
class DownloadItem:
    """Represents one scene download target and its local status."""

    scene_name: str
    url: str
    expected_bytes: int | None
    filename: str
    dest_path: Path
    status: str
    existing_bytes: int


def strip_auth_if_aws(response, *args, **kwargs):
    """Remove auth headers on redirects to AWS URLs.

    Args:
        response: HTTP response object from ASF session.
        *args: Unused response hook positional args.
        **kwargs: Unused response hook keyword args.
    """
    if (
        300 <= response.status_code <= 399
        and "location" in response.headers
        and "amazonaws.com" in urlparse(response.headers["location"]).netloc
    ):
        location = response.headers["location"]
        response.headers.clear()
        response.headers["location"] = location


def read_earthdata_creds() -> tuple[str, str]:
    """Read Earthdata credentials from `~/.netrc`.

    Returns:
        Tuple of (username, password).

    Raises:
        RuntimeError: If valid Earthdata credentials are not found.
    """
    machine_candidates = [
        "urs.earthdata.nasa.gov",
        "urs.earthdata.nasa.gov:443",
        "earthdata.nasa.gov",
    ]
    nrc = netrc.netrc()

    for machine in machine_candidates:
        auth = nrc.authenticators(machine)
        if auth:
            login, account, password = auth
            username = login or account
            if username and password:
                return username, password

    raise RuntimeError(
        "No Earthdata credentials found in ~/.netrc. "
        "Add: machine urs.earthdata.nasa.gov login <username> password <password>"
    )


def parse_csv_items(scene_csv: Path, slc_dir: Path) -> list[DownloadItem]:
    """Build download items from scene metadata CSV and local file status.

    Args:
        scene_csv: Path to scenes CSV file.
        slc_dir: Directory where scene ZIP files are stored.

    Returns:
        List of download items with status fields populated.

    Raises:
        ValueError: If a scene row has no URL.
    """
    items: list[DownloadItem] = []

    with scene_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scene_name = row.get("sceneName", "").strip()
            url = row.get("url", "").strip()
            bytes_str = row.get("bytes", "").strip()
            expected_bytes = int(bytes_str) if bytes_str else None

            if not url:
                raise ValueError(f"Missing URL for scene: {scene_name}")

            filename = Path(urlparse(url).path).name
            if not filename:
                filename = f"{scene_name}.zip"

            dest_path = slc_dir / filename

            if dest_path.exists():
                existing_bytes = dest_path.stat().st_size
                if expected_bytes is not None and existing_bytes == expected_bytes:
                    status = "complete"
                else:
                    status = "partial"
            else:
                existing_bytes = 0
                status = "missing"

            items.append(
                DownloadItem(
                    scene_name=scene_name,
                    url=url,
                    expected_bytes=expected_bytes,
                    filename=filename,
                    dest_path=dest_path,
                    status=status,
                    existing_bytes=existing_bytes,
                )
            )

    return items


def load_manifest(path: Path) -> dict:
    """Load download manifest JSON.

    Args:
        path: Manifest file path.

    Returns:
        Existing manifest content or default empty manifest.
    """
    if not path.exists():
        return {"downloads": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict) -> None:
    """Write download manifest JSON to disk.

    Args:
        path: Manifest file path.
        manifest: Manifest dictionary.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def update_manifest(manifest: dict, item: DownloadItem, status: str, message: str = "") -> None:
    """Update one scene entry in manifest with latest status.

    Args:
        manifest: Manifest dictionary to mutate.
        item: Scene download item.
        status: Status label to store.
        message: Optional status message or error text.
    """
    now = datetime.now(timezone.utc).isoformat()
    manifest["downloads"][item.scene_name] = {
        "scene_name": item.scene_name,
        "filename": item.filename,
        "url": item.url,
        "status": status,
        "message": message,
        "updated_utc": now,
        "expected_bytes": item.expected_bytes,
        "existing_bytes": item.dest_path.stat().st_size if item.dest_path.exists() else 0,
    }


def short_scene_name(scene_name: str, max_len: int = 44) -> str:
    """Shorten a long scene name for progress bar display.

    Args:
        scene_name: Full scene name string.
        max_len: Maximum output length.

    Returns:
        Scene label truncated with an ellipsis when needed.
    """
    if len(scene_name) <= max_len:
        return scene_name
    keep = max_len - 3
    left = keep // 2
    right = keep - left
    return f"{scene_name[:left]}...{scene_name[-right:]}"


def iter_polygons(geometry: Any):
    """Yield polygon members from a shapely geometry."""
    geom_type = geometry.geom_type
    if geom_type == "Polygon":
        yield geometry
        return
    if geom_type == "MultiPolygon":
        for poly in geometry.geoms:
            yield poly
        return
    if geom_type == "GeometryCollection":
        for sub in geometry.geoms:
            yield from iter_polygons(sub)


def write_stack_download_map(
    *,
    stack_geojson_path: Path,
    aoi_points: list[tuple[float, float]],
    out_png: Path,
    dpi: int,
) -> dict[str, Any]:
    """Write a footprint map for the selected stack scene set.

    Args:
        stack_geojson_path: GeoJSON path from stack search (`outputs.geojson`).
        aoi_points: AOI polygon lon/lat points from project KML.
        out_png: Output PNG path.
        dpi: Output PNG DPI.

    Returns:
        Small metadata dictionary describing the generated map.

    Raises:
        RuntimeError: If geometry/map dependencies are unavailable or inputs invalid.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import contextily as ctx
        from shapely.geometry import shape
        from shapely.ops import transform, unary_union
        from pyproj import Transformer
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency for map generation "
            "(matplotlib/contextily/shapely/pyproj)."
        ) from exc

    if not stack_geojson_path.exists():
        raise RuntimeError(f"Missing stack GeoJSON: {stack_geojson_path}")

    payload = json.loads(stack_geojson_path.read_text(encoding="utf-8"))
    features = payload.get("features", []) if isinstance(payload, dict) else []
    if not features:
        raise RuntimeError(f"No features found in stack GeoJSON: {stack_geojson_path}")

    to_mercator = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

    geoms_mercator: list[Any] = []
    unique_dates: set[str] = set()
    for feature in features:
        props = feature.get("properties", {}) or {}
        start_time = str(props.get("startTime", "")).strip()
        if len(start_time) >= 10:
            unique_dates.add(start_time[:10])

        geometry_data = feature.get("geometry")
        if not geometry_data:
            continue
        geom = shape(geometry_data)
        if geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
            if geom.is_empty:
                continue
        geoms_mercator.append(transform(to_mercator.transform, geom))

    if not geoms_mercator:
        raise RuntimeError(f"No valid geometries found in {stack_geojson_path}")

    stack_union = unary_union(geoms_mercator)

    aoi_mercator = [to_mercator.transform(x, y) for x, y in aoi_points]
    aoi_x = [p[0] for p in aoi_mercator]
    aoi_y = [p[1] for p in aoi_mercator]

    minx, miny, maxx, maxy = stack_union.bounds
    minx = min(minx, min(aoi_x))
    miny = min(miny, min(aoi_y))
    maxx = max(maxx, max(aoi_x))
    maxy = max(maxy, max(aoi_y))
    dx = maxx - minx
    dy = maxy - miny
    pad_x = max(dx * 0.08, 1.0)
    pad_y = max(dy * 0.08, 1.0)

    fig, ax = plt.subplots(figsize=(11.69, 8.27), dpi=110)
    ax.set_facecolor("#f7f8fa")

    ax.set_xlim(minx - pad_x, maxx + pad_x)
    ax.set_ylim(miny - pad_y, maxy + pad_y)
    ax.set_aspect("equal", adjustable="box")

    try:
        image, extent = ctx.bounds2img(
            minx - pad_x,
            miny - pad_y,
            maxx + pad_x,
            maxy + pad_y,
            source=ctx.providers.CartoDB.Positron,
            ll=False,
            use_cache=False,
            n_connections=1,
        )
        ax.imshow(
            image,
            extent=extent,
            interpolation="bilinear",
            aspect=ax.get_aspect(),
            zorder=0,
        )
        ax.set_xlim(minx - pad_x, maxx + pad_x)
        ax.set_ylim(miny - pad_y, maxy + pad_y)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Could not add Positron basemap via contextily: {exc}"
        ) from exc

    # Draw each scene footprint lightly, then the combined stack envelope on top.
    for geom in geoms_mercator:
        for poly in iter_polygons(geom):
            x, y = poly.exterior.xy
            ax.fill(x, y, color="#fb8500", alpha=0.05, zorder=2)
            ax.plot(x, y, color="#ffb703", linewidth=0.9, alpha=0.35, zorder=3)

    for poly in iter_polygons(stack_union):
        x, y = poly.exterior.xy
        ax.fill(x, y, color="#fb8500", alpha=0.17, zorder=4)
        ax.plot(x, y, color="white", linewidth=3.2, alpha=0.95, zorder=5)
        ax.plot(x, y, color="#d00000", linewidth=1.7, alpha=0.95, zorder=6)

    ax.fill(aoi_x, aoi_y, color="#0077b6", alpha=0.14, zorder=7)
    ax.plot(aoi_x, aoi_y, color="white", linewidth=4.0, zorder=8)
    ax.plot(aoi_x, aoi_y, color="#0077b6", linewidth=2.2, zorder=9)

    ax.grid(color="#d9dee6", linestyle="--", linewidth=0.6, alpha=0.6)
    ax.set_xlabel("Web Mercator X (m)")
    ax.set_ylabel("Web Mercator Y (m)")
    ax.set_title(
        "Downloaded Stack Footprint vs Project AOI",
        fontsize=13,
        fontweight="bold",
        pad=10,
    )

    date_span = ""
    if unique_dates:
        ordered_dates = sorted(unique_dates)
        date_span = f"{ordered_dates[0]} -> {ordered_dates[-1]}"
    subtitle = (
        f"Scenes: {len(features)} | Unique dates: {len(unique_dates)}"
        + (f" | Date span: {date_span}" if date_span else "")
    )
    ax.text(
        0.01,
        0.99,
        subtitle,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9.3,
        bbox={"facecolor": "white", "edgecolor": "#d1d5db", "alpha": 0.92, "pad": 4.0},
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=max(100, dpi), bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)

    return {
        "map_png": str(out_png),
        "scene_count": len(features),
        "unique_dates": len(unique_dates),
        "date_span": date_span,
        "basemap": "CartoDB.Positron (contextily,no-cache)",
        "basemap_added": True,
    }


def download_url_with_progress(
    item: DownloadItem,
    session: asf.ASFSession,
    stack_bytes_bar: tqdm | None,
    scene_index: int,
    scene_total: int,
    request_timeout_seconds: int,
    position: int = 2,
) -> None:
    """Download one scene URL to disk and update progress bars.

    Args:
        item: Scene metadata and output path.
        session: Authenticated ASF session.
        stack_bytes_bar: Shared cumulative bytes progress bar.
        scene_index: 1-based scene position in current run.
        scene_total: Total pending scenes in current run.
        request_timeout_seconds: HTTP timeout for request operations.
        position: TQDM display row index for per-file bar.

    Raises:
        RuntimeError: If HTTP request fails.
    """
    response = session.get(
        item.url,
        stream=True,
        hooks={"response": strip_auth_if_aws},
        timeout=request_timeout_seconds,
    )
    try:
        response.raise_for_status()
    except Exception as e:  # noqa: BLE001
        body_preview = ""
        try:
            body_preview = response.text[:300]
        except Exception:  # noqa: BLE001
            body_preview = ""
        raise RuntimeError(
            f"HTTP {response.status_code} while downloading {item.scene_name}. {body_preview}"
        ) from e

    header_len = response.headers.get("Content-Length") or response.headers.get("content-length")
    header_total = int(header_len) if header_len and header_len.isdigit() else None
    file_total = header_total or item.expected_bytes

    scene_label = short_scene_name(item.scene_name)
    desc = f"SLC {scene_index}/{scene_total} {scene_label}"
    with (
        item.dest_path.open("wb") as f,
        tqdm(
            total=file_total,
            desc=desc,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            dynamic_ncols=True,
            leave=False,
            mininterval=0.2,
            position=position,
        ) as file_bar,
    ):
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            n = len(chunk)
            file_bar.update(n)
            if stack_bytes_bar is not None:
                stack_bytes_bar.update(n)


def main() -> int:
    """Parse CLI args and run dry-run planning or actual downloads.

    Why:
        Stage raw SLC inputs safely before DEM/orbit-aware preprocessing and
        coregistration.

    Technical details:
        - Computes pending scenes from local file size vs expected bytes.
        - Performs free-space checks with reserve/overhead margins.
        - Uses streaming HTTP downloads with per-file and stack-level progress.
        - Records per-scene status transitions in a JSON manifest.

    Returns:
        Exit code (0 for success).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Download selected Sentinel-1 SLC scenes listed in stack search outputs. "
            "Default mode is dry-run summary."
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
        "--download",
        action="store_true",
        help="Execute downloads. Without this flag, only print a dry-run plan.",
    )
    parser.add_argument(
        "--max-scenes",
        type=int,
        default=0,
        help="Optional cap for number of pending scenes to download (0 = no cap).",
    )
    parser.add_argument(
        "--reserve-gb",
        type=float,
        default=20.0,
        help="Required free-space reserve in GB after planned downloads.",
    )
    parser.add_argument(
        "--overhead-fraction",
        type=float,
        default=0.05,
        help="Extra space multiplier on pending download size for safety checks.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore space guard checks and proceed.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=int,
        default=120,
        help="HTTP request timeout in seconds for ASF downloads.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Number of retry attempts per scene after an initial failure.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=5.0,
        help="Base backoff delay (seconds); retries use exponential backoff.",
    )
    parser.add_argument(
        "--stack-map-png",
        default="",
        help=(
            "Output PNG for selected stack footprint map. "
            "Default: <outputs.root>/products/download_stack_footprint.png"
        ),
    )
    parser.add_argument(
        "--no-stack-map",
        action="store_true",
        help="Disable stack footprint map PNG creation in dry-run and --download modes.",
    )
    parser.add_argument(
        "--map-dpi",
        type=int,
        default=260,
        help="Stack footprint map resolution in DPI.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    try:
        config_path = resolve_stack_config(repo_root, args.config)
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    cfg = read_toml(config_path)
    out_cfg = cfg["outputs"]
    storage_cfg = cfg["storage"]

    out_root = resolve_path(repo_root, out_cfg["root"])
    scene_csv = out_root / out_cfg["metadata_csv"]
    geojson_path = out_root / out_cfg["geojson"]
    slc_dir = resolve_path(repo_root, storage_cfg["slc_dir"])
    manifest_path = resolve_path(repo_root, storage_cfg["download_manifest"])
    slc_dir.mkdir(parents=True, exist_ok=True)

    if not scene_csv.exists():
        print(f"Missing scene metadata CSV: {scene_csv}", file=sys.stderr)
        print("Run stack search first.", file=sys.stderr)
        return 2

    items = parse_csv_items(scene_csv, slc_dir)
    pending = [i for i in items if i.status in {"missing", "partial"}]
    complete = [i for i in items if i.status == "complete"]

    if args.max_scenes > 0:
        pending = pending[: args.max_scenes]

    pending_known_bytes = sum(i.expected_bytes or 0 for i in pending)
    unknown_size_count = sum(1 for i in pending if i.expected_bytes is None)

    usage = shutil.disk_usage(slc_dir)
    free_bytes = usage.free
    reserve_bytes = int(args.reserve_gb * 1e9)
    guarded_required = int(pending_known_bytes * (1.0 + args.overhead_fraction)) + reserve_bytes

    print(f"Config: {config_path}")
    print(f"Scene CSV: {scene_csv}")
    print(f"Stack GeoJSON: {geojson_path}")
    print(f"SLC dir: {slc_dir}")
    print(f"Manifest: {manifest_path}")
    print(f"Total listed scenes: {len(items)}")
    print(f"Already complete: {len(complete)}")
    print(f"Pending: {len(pending)}")
    print(f"Pending known size: {pending_known_bytes / 1e9:.2f} GB (decimal)")
    print(f"Pending unknown-size scenes: {unknown_size_count}")
    print(f"Free space at target FS: {free_bytes / 1e9:.2f} GB (decimal)")
    print(
        "Guarded required space "
        f"(pending * (1+{args.overhead_fraction:.2f}) + reserve {args.reserve_gb:.1f} GB): "
        f"{guarded_required / 1e9:.2f} GB"
    )

    stack_map_path: Path | None = None
    if not args.no_stack_map:
        if args.stack_map_png:
            stack_map_path = resolve_path(repo_root, args.stack_map_png)
        else:
            stack_map_path = out_root / "products" / "download_stack_footprint.png"
        print(f"Stack map PNG: {stack_map_path}")

    if stack_map_path is not None:
        try:
            aoi_kml = resolve_path(repo_root, cfg["aoi"]["kml"])
            aoi_points = parse_kml_points(aoi_kml)
            map_meta = write_stack_download_map(
                stack_geojson_path=geojson_path,
                aoi_points=aoi_points,
                out_png=stack_map_path,
                dpi=max(100, args.map_dpi),
            )
            print(
                "Wrote stack map PNG: "
                f"{stack_map_path} "
                f"(scenes={map_meta['scene_count']}, unique_dates={map_meta['unique_dates']})"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] Could not generate stack map PNG: {exc}", file=sys.stderr)

    if not args.download:
        print("\nDry-run only. Re-run with --download to execute.")
        return 0

    if not args.force and pending_known_bytes > 0 and free_bytes < guarded_required:
        print(
            "\nAborting: free space below guarded requirement. "
            "Use --force only if you understand the risk.",
            file=sys.stderr,
        )
        return 3

    if args.request_timeout_seconds <= 0:
        print("--request-timeout-seconds must be > 0.", file=sys.stderr)
        return 2
    if args.max_retries < 0:
        print("--max-retries must be >= 0.", file=sys.stderr)
        return 2
    if args.retry_backoff_seconds < 0:
        print("--retry-backoff-seconds must be >= 0.", file=sys.stderr)
        return 2

    username, password = read_earthdata_creds()
    session = asf.ASFSession().auth_with_creds(username, password)
    manifest = load_manifest(manifest_path)

    stack_scene_bar = tqdm(
        total=len(pending),
        desc="Stack scenes",
        unit="scene",
        dynamic_ncols=True,
        mininterval=0.2,
        position=0,
    )
    stack_bytes_total = pending_known_bytes if unknown_size_count == 0 and pending_known_bytes > 0 else None
    stack_bytes_bar = tqdm(
        total=stack_bytes_total,
        desc="Stack bytes",
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        dynamic_ncols=True,
        mininterval=0.2,
        position=1,
    )

    try:
        for idx, item in enumerate(pending, start=1):
            scene_label = short_scene_name(item.scene_name)
            stack_scene_bar.set_postfix_str(f"now={scene_label}")

            if item.dest_path.exists():
                current_size = item.dest_path.stat().st_size
                if item.expected_bytes is not None and current_size != item.expected_bytes:
                    # Remove partial files because current workflow always restarts the file.
                    item.dest_path.unlink()

            try:
                total_attempts = args.max_retries + 1
                for attempt in range(1, total_attempts + 1):
                    try:
                        download_url_with_progress(
                            item=item,
                            session=session,
                            stack_bytes_bar=stack_bytes_bar,
                            scene_index=idx,
                            scene_total=len(pending),
                            request_timeout_seconds=args.request_timeout_seconds,
                            position=2,
                        )

                        if item.expected_bytes is not None:
                            got = item.dest_path.stat().st_size if item.dest_path.exists() else 0
                            if got != item.expected_bytes:
                                raise RuntimeError(
                                    f"Size mismatch for {item.filename}: expected {item.expected_bytes}, got {got}"
                                )
                        break
                    except Exception:  # noqa: BLE001
                        if attempt >= total_attempts:
                            raise
                        wait_seconds = args.retry_backoff_seconds * (2 ** (attempt - 1))
                        tqdm.write(
                            "Retrying "
                            f"{item.scene_name} "
                            f"(attempt {attempt + 1}/{total_attempts}) "
                            f"after {wait_seconds:.1f}s..."
                        )
                        if wait_seconds > 0:
                            time.sleep(wait_seconds)

                update_manifest(manifest, item, status="downloaded")
                save_manifest(manifest_path, manifest)
                stack_scene_bar.update(1)
                stack_scene_bar.set_postfix_str(f"last={scene_label}")
            except Exception as e:  # noqa: BLE001
                update_manifest(manifest, item, status="failed", message=str(e))
                save_manifest(manifest_path, manifest)
                tqdm.write(f"Download failed for {item.scene_name}: {e}")
                return 4
    finally:
        stack_scene_bar.close()
        stack_bytes_bar.close()

    print("\nDownload stage completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
