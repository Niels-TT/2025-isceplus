#!/usr/bin/env python3
"""Export Dolphin raster outputs into operational point products (CSV + KMZ).

Technical summary:
    Reads Dolphin velocity raster and optional quality masks, selects valid
    points, and writes:
    1) CSV point table for analytics workflows.
    2) KMZ point layer for Google Earth Pro (3D via relative altitude).

Why:
    Dolphin intentionally outputs geocoded rasters. Operational workflows often
    need sparse point deliverables for map tools and field operations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import xy
from rasterio.warp import transform as rio_transform

from stack_common import DEFAULT_STACK_CONFIG_REL, read_toml, resolve_path


def bool_cfg(cfg: dict[str, Any], key: str, default: bool) -> bool:
    """Read a boolean-like value from config."""
    return bool(cfg.get(key, default))


def int_cfg(cfg: dict[str, Any], key: str, default: int) -> int:
    """Read an integer config value with fallback."""
    val = cfg.get(key, default)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def float_cfg(cfg: dict[str, Any], key: str, default: float) -> float:
    """Read a float config value with fallback."""
    val = cfg.get(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def str_cfg(cfg: dict[str, Any], key: str, default: str) -> str:
    """Read a string config value with fallback."""
    val = cfg.get(key, default)
    text = str(val).strip() if val is not None else ""
    return text if text else default


def auto_find_temporal_coherence(work_dir: Path) -> Path | None:
    """Find a likely temporal coherence raster under Dolphin work directory."""
    candidates = sorted((work_dir / "interferograms").glob("temporal_coherence_*.tif"))
    if candidates:
        return candidates[-1]
    linked = sorted(work_dir.glob("t*/linked_phase/temporal_coherence_*.tif"))
    if linked:
        return linked[-1]
    return None


def auto_find_ps_mask(work_dir: Path) -> Path | None:
    """Find a likely PS mask raster under Dolphin work directory."""
    candidates = sorted(work_dir.glob("t*/PS/ps_pixels.tif"))
    if candidates:
        return candidates[0]
    return None


def kml_color_from_rgb(r: int, g: int, b: int, alpha: int = 220) -> str:
    """Convert RGBA (0..255) to KML color string (AABBGGRR)."""
    return f"{alpha:02x}{b:02x}{g:02x}{r:02x}"


def velocity_to_rgb(v_mm_yr: float, clip_abs_mm_yr: float) -> tuple[int, int, int]:
    """Map velocity to a diverging blue-white-red color."""
    if clip_abs_mm_yr <= 0:
        clip_abs_mm_yr = 1.0
    norm = np.clip(v_mm_yr / clip_abs_mm_yr, -1.0, 1.0)
    if norm < 0:
        # Blue -> White
        t = norm + 1.0
        r = int(255 * t)
        g = int(255 * t)
        b = 255
    else:
        # White -> Red
        t = norm
        r = 255
        g = int(255 * (1.0 - t))
        b = int(255 * (1.0 - t))
    return r, g, b


def xml_escape(text: str) -> str:
    """Escape XML-sensitive characters for KML text nodes."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


@dataclass
class SelectedPoints:
    """Container for selected output points."""

    rows: np.ndarray
    cols: np.ndarray
    xs: np.ndarray
    ys: np.ndarray
    lons: np.ndarray
    lats: np.ndarray
    vel_m_yr: np.ndarray
    vel_mm_yr: np.ndarray
    coh: np.ndarray | None
    is_ps: np.ndarray | None


def ensure_same_grid(base: rasterio.DatasetReader, other: rasterio.DatasetReader) -> bool:
    """Check if two rasters are on the same pixel grid."""
    return (
        base.width == other.width
        and base.height == other.height
        and base.transform == other.transform
        and base.crs == other.crs
    )


def select_points(
    velocity_file: Path,
    coherence_file: Path | None,
    ps_mask_file: Path | None,
    min_temporal_coherence: float,
    use_ps_mask: bool,
    stride: int,
    max_points: int,
) -> tuple[SelectedPoints, dict[str, Any]]:
    """Select valid points from Dolphin rasters.

    Args:
        velocity_file: Velocity raster in meters/year.
        coherence_file: Optional temporal coherence raster.
        ps_mask_file: Optional PS mask raster.
        min_temporal_coherence: Minimum temporal coherence threshold.
        use_ps_mask: Whether to keep only PS pixels.
        stride: Regular pixel decimation stride.
        max_points: Max point count after selection.

    Returns:
        Selected points and selection statistics dictionary.
    """
    with rasterio.open(velocity_file) as vel_ds:
        vel = vel_ds.read(1).astype(np.float64)
        nodata = vel_ds.nodata
        valid = np.isfinite(vel)
        if nodata is not None:
            valid &= vel != nodata

        coh_arr: np.ndarray | None = None
        if coherence_file is not None and coherence_file.exists():
            with rasterio.open(coherence_file) as coh_ds:
                if ensure_same_grid(vel_ds, coh_ds):
                    coh_arr = coh_ds.read(1).astype(np.float64)
                    coh_nodata = coh_ds.nodata
                    coh_valid = np.isfinite(coh_arr)
                    if coh_nodata is not None:
                        coh_valid &= coh_arr != coh_nodata
                    valid &= coh_valid
                    valid &= coh_arr >= min_temporal_coherence
                else:
                    coh_arr = None

        ps_arr: np.ndarray | None = None
        if ps_mask_file is not None and ps_mask_file.exists():
            with rasterio.open(ps_mask_file) as ps_ds:
                if ensure_same_grid(vel_ds, ps_ds):
                    ps_arr = ps_ds.read(1).astype(np.float64)
                    if use_ps_mask:
                        valid &= ps_arr > 0
                else:
                    ps_arr = None

        if stride < 1:
            stride = 1
        if stride > 1:
            stride_mask = np.zeros_like(valid, dtype=bool)
            stride_mask[::stride, ::stride] = True
            valid &= stride_mask

        candidate_count = int(np.count_nonzero(valid))
        if candidate_count == 0:
            raise RuntimeError("No valid points after quality filtering.")

        rows, cols = np.where(valid)
        if max_points > 0 and rows.size > max_points:
            step = math.ceil(rows.size / max_points)
            keep = np.arange(0, rows.size, step, dtype=np.int64)
            rows = rows[keep]
            cols = cols[keep]

        xs, ys = xy(vel_ds.transform, rows, cols, offset="center")
        xs_arr = np.asarray(xs, dtype=np.float64)
        ys_arr = np.asarray(ys, dtype=np.float64)
        if vel_ds.crs is not None:
            lons, lats = rio_transform(vel_ds.crs, "EPSG:4326", xs_arr.tolist(), ys_arr.tolist())
            lons_arr = np.asarray(lons, dtype=np.float64)
            lats_arr = np.asarray(lats, dtype=np.float64)
        else:
            # Fallback: assume raster is already lon/lat when CRS is absent.
            lons_arr = xs_arr
            lats_arr = ys_arr
        vel_m = vel[rows, cols]
        vel_mm = vel_m * 1000.0
        coh_sel = coh_arr[rows, cols] if coh_arr is not None else None
        is_ps_sel = (ps_arr[rows, cols] > 0) if ps_arr is not None else None

    stats = {
        "candidates_after_filters": candidate_count,
        "selected_points": int(rows.size),
        "stride": stride,
        "max_points": max_points,
    }
    return (
        SelectedPoints(
            rows=rows.astype(np.int64),
            cols=cols.astype(np.int64),
            xs=xs_arr,
            ys=ys_arr,
            lons=lons_arr,
            lats=lats_arr,
            vel_m_yr=vel_m.astype(np.float64),
            vel_mm_yr=vel_mm.astype(np.float64),
            coh=coh_sel.astype(np.float64) if coh_sel is not None else None,
            is_ps=is_ps_sel.astype(bool) if is_ps_sel is not None else None,
        ),
        stats,
    )


def write_csv(points: SelectedPoints, out_csv: Path, altitude_scale: float) -> None:
    """Write selected points to CSV."""
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "id",
                "x_coord",
                "y_coord",
                "lon",
                "lat",
                "row",
                "col",
                "velocity_m_per_year",
                "velocity_mm_per_year",
                "temporal_coherence",
                "is_ps",
                "kmz_altitude_m",
            ]
        )
        for idx in range(points.rows.size):
            coh_val = float(points.coh[idx]) if points.coh is not None else ""
            is_ps = bool(points.is_ps[idx]) if points.is_ps is not None else ""
            alt_m = float(points.vel_mm_yr[idx] * altitude_scale)
            writer.writerow(
                [
                    idx + 1,
                    f"{points.xs[idx]:.3f}",
                    f"{points.ys[idx]:.3f}",
                    f"{points.lons[idx]:.8f}",
                    f"{points.lats[idx]:.8f}",
                    int(points.rows[idx]),
                    int(points.cols[idx]),
                    f"{points.vel_m_yr[idx]:.8f}",
                    f"{points.vel_mm_yr[idx]:.3f}",
                    f"{coh_val:.4f}" if coh_val != "" else "",
                    is_ps,
                    f"{alt_m:.3f}",
                ]
            )


def build_kml(points: SelectedPoints, altitude_scale: float, clip_abs_mm_yr: float, name_prefix: str) -> str:
    """Build KML document string for selected points."""
    styles: list[str] = []
    for i in range(256):
        v = -clip_abs_mm_yr + (2.0 * clip_abs_mm_yr * i / 255.0)
        r, g, b = velocity_to_rgb(v, clip_abs_mm_yr)
        color = kml_color_from_rgb(r, g, b, alpha=220)
        styles.append(
            f'<Style id="v{i}"><IconStyle><color>{color}</color><scale>0.55</scale>'
            f"<Icon><href>http://maps.google.com/mapfiles/kml/shapes/shaded_dot.png</href></Icon>"
            f"</IconStyle><LineStyle><color>{color}</color><width>1</width></LineStyle></Style>"
        )

    header = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "<Document>",
        f"<name>{xml_escape(name_prefix)} velocity points</name>",
        "<open>1</open>",
        *styles,
        "<Folder><name>Velocity Points</name><open>1</open>",
    ]

    placemarks: list[str] = []
    denom = clip_abs_mm_yr if clip_abs_mm_yr > 0 else 1.0
    for idx in range(points.rows.size):
        v_mm = float(points.vel_mm_yr[idx])
        norm = np.clip((v_mm + denom) / (2.0 * denom), 0.0, 1.0)
        style_idx = int(round(norm * 255.0))
        alt = float(v_mm * altitude_scale)
        coh_txt = f"{float(points.coh[idx]):.3f}" if points.coh is not None else "n/a"
        ps_txt = "1" if (points.is_ps is not None and bool(points.is_ps[idx])) else "0"
        desc = (
            f"Velocity: {v_mm:.2f} mm/yr\\n"
            f"Coherence: {coh_txt}\\n"
            f"PS: {ps_txt}\\n"
            f"Pixel row/col: {int(points.rows[idx])}/{int(points.cols[idx])}"
        )
        placemarks.append(
            "<Placemark>"
            f"<name>{xml_escape(f'{name_prefix}_{idx + 1:06d}')}</name>"
            f"<description>{xml_escape(desc)}</description>"
            f"<styleUrl>#v{style_idx}</styleUrl>"
            "<Point><extrude>1</extrude><altitudeMode>relativeToGround</altitudeMode>"
            f"<coordinates>{points.lons[idx]:.8f},{points.lats[idx]:.8f},{alt:.3f}</coordinates>"
            "</Point></Placemark>"
        )

    footer = ["</Folder>", "</Document>", "</kml>"]
    return "\n".join([*header, *placemarks, *footer]) + "\n"


def write_kmz(points: SelectedPoints, out_kmz: Path, altitude_scale: float, clip_abs_mm_yr: float, name_prefix: str) -> None:
    """Write selected points to KMZ for Google Earth Pro."""
    out_kmz.parent.mkdir(parents=True, exist_ok=True)
    kml_text = build_kml(
        points=points,
        altitude_scale=altitude_scale,
        clip_abs_mm_yr=clip_abs_mm_yr,
        name_prefix=name_prefix,
    )
    with zipfile.ZipFile(out_kmz, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml_text.encode("utf-8"))


def main() -> int:
    """Parse CLI args and export point products from Dolphin outputs."""
    parser = argparse.ArgumentParser(
        description="Export Dolphin velocity raster to CSV + KMZ point products."
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
        help="Validate inputs and print planned outputs without writing files.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    stack_config = resolve_path(repo_root, args.config)
    cfg = read_toml(stack_config)
    dolphin_cfg = cfg.get("processing", {}).get("dolphin", {})
    export_cfg = dolphin_cfg.get("point_exports", {})

    enabled = bool_cfg(export_cfg, "enabled", False)
    if not enabled:
        print("Point export disabled (processing.dolphin.point_exports.enabled=false).")
        return 0

    work_dir = resolve_path(
        repo_root,
        str_cfg(
            dolphin_cfg,
            "work_dir",
            "miami/insar/us_isleofnormandy_s1_asc_t48/stack/dolphin",
        ),
    )
    output_dir = resolve_path(
        repo_root,
        str_cfg(export_cfg, "output_dir", str(work_dir / "exports")),
    )
    velocity_file = resolve_path(
        repo_root,
        str_cfg(export_cfg, "velocity_file", str(work_dir / "timeseries" / "velocity.tif")),
    )
    coherence_file_cfg = str(export_cfg.get("coherence_file", "")).strip()
    coherence_file = (
        resolve_path(repo_root, coherence_file_cfg)
        if coherence_file_cfg
        else auto_find_temporal_coherence(work_dir)
    )
    ps_mask_cfg = str(export_cfg.get("ps_mask_file", "")).strip()
    ps_mask_file = (
        resolve_path(repo_root, ps_mask_cfg)
        if ps_mask_cfg
        else auto_find_ps_mask(work_dir)
    )

    csv_enabled = bool_cfg(export_cfg, "csv_enabled", True)
    kmz_enabled = bool_cfg(export_cfg, "kmz_enabled", True)
    if not csv_enabled and not kmz_enabled:
        print("Both csv_enabled and kmz_enabled are false; nothing to export.")
        return 0

    min_coh = float_cfg(export_cfg, "min_temporal_coherence", 0.6)
    use_ps_mask = bool_cfg(export_cfg, "use_ps_mask", False)
    stride = int_cfg(export_cfg, "stride", 4)
    max_points = int_cfg(export_cfg, "max_points", 80000)
    altitude_scale = float_cfg(export_cfg, "altitude_scale_m_per_mm_per_year", 3.0)
    clip_abs = float_cfg(export_cfg, "color_clip_abs_mm_per_year", 30.0)
    name_prefix = str_cfg(export_cfg, "name_prefix", "dolphin")
    csv_file = resolve_path(
        repo_root,
        str_cfg(export_cfg, "csv_file", str(output_dir / "velocity_points.csv")),
    )
    kmz_file = resolve_path(
        repo_root,
        str_cfg(export_cfg, "kmz_file", str(output_dir / "velocity_points.kmz")),
    )

    if not velocity_file.exists():
        print(f"Missing velocity raster: {velocity_file}", file=sys.stderr)
        print("Run Dolphin first or set processing.dolphin.point_exports.velocity_file.", file=sys.stderr)
        return 2

    print(f"Config: {stack_config}")
    print(f"Dolphin work dir: {work_dir}")
    print(f"Velocity file: {velocity_file}")
    print(f"Coherence file: {coherence_file if coherence_file else 'none'}")
    print(f"PS mask file: {ps_mask_file if ps_mask_file else 'none'}")
    print(f"Output dir: {output_dir}")
    print(f"CSV enabled: {csv_enabled} -> {csv_file}")
    print(f"KMZ enabled: {kmz_enabled} -> {kmz_file}")
    print(f"Filters: min_coherence={min_coh}, use_ps_mask={use_ps_mask}, stride={stride}, max_points={max_points}")
    print(f"KMZ altitude scale (m per mm/yr): {altitude_scale}")
    print(f"KMZ color clip abs (mm/yr): {clip_abs}")

    if args.dry_run:
        return 0

    points, stats = select_points(
        velocity_file=velocity_file,
        coherence_file=coherence_file,
        ps_mask_file=ps_mask_file,
        min_temporal_coherence=min_coh,
        use_ps_mask=use_ps_mask,
        stride=stride,
        max_points=max_points,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    if csv_enabled:
        write_csv(points, csv_file, altitude_scale=altitude_scale)
    if kmz_enabled:
        write_kmz(
            points,
            kmz_file,
            altitude_scale=altitude_scale,
            clip_abs_mm_yr=clip_abs,
            name_prefix=name_prefix,
        )

    summary = {
        "exported_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(stack_config),
        "work_dir": str(work_dir),
        "velocity_file": str(velocity_file),
        "coherence_file": str(coherence_file) if coherence_file else None,
        "ps_mask_file": str(ps_mask_file) if ps_mask_file else None,
        "csv_file": str(csv_file) if csv_enabled else None,
        "kmz_file": str(kmz_file) if kmz_enabled else None,
        "min_temporal_coherence": min_coh,
        "use_ps_mask": use_ps_mask,
        "stride": stride,
        "max_points": max_points,
        "altitude_scale_m_per_mm_per_year": altitude_scale,
        "color_clip_abs_mm_per_year": clip_abs,
        "stats": stats,
    }
    summary_path = output_dir / "export_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Selected points: {stats['selected_points']} (from {stats['candidates_after_filters']}).")
    print(f"Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
