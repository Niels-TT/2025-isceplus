#!/usr/bin/env python3
"""Export Dolphin velocity raster to quick-look visualization products.

Technical summary:
    Reads Dolphin velocity raster (m/yr), colorizes it with a fixed diverging
    scale (mm/yr), and writes:
    1) Colorized GeoTIFF (for QGIS).
    2) KMZ ground overlay + legend (for Google Earth Pro).

Why:
    Keep raster products as the scientific source while providing immediate,
    interpretable visual outputs for inspection and communication.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.transform import array_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling

from stack_common import DEFAULT_STACK_CONFIG_REL, read_toml, resolve_path


def bool_cfg(cfg: dict[str, Any], key: str, default: bool) -> bool:
    """Read a boolean-like config value with fallback."""
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


def velocity_to_rgb(vel_mm_yr: np.ndarray, clip_abs_mm_yr: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Map velocity (mm/yr) to diverging blue-white-red RGB arrays.

    Args:
        vel_mm_yr: Velocity values in mm/yr.
        clip_abs_mm_yr: Symmetric clipping range in mm/yr.

    Returns:
        Tuple of uint8 arrays (R, G, B).
    """
    clip = max(float(clip_abs_mm_yr), 1e-6)
    norm = np.clip(vel_mm_yr / clip, -1.0, 1.0)

    r = np.empty(norm.shape, dtype=np.float32)
    g = np.empty(norm.shape, dtype=np.float32)
    b = np.empty(norm.shape, dtype=np.float32)

    neg = norm < 0
    pos = ~neg

    # Negative side: blue -> white
    t_neg = norm[neg] + 1.0
    r[neg] = 255.0 * t_neg
    g[neg] = 255.0 * t_neg
    b[neg] = 255.0

    # Positive side: white -> red
    t_pos = norm[pos]
    r[pos] = 255.0
    g[pos] = 255.0 * (1.0 - t_pos)
    b[pos] = 255.0 * (1.0 - t_pos)

    return (
        np.clip(r, 0, 255).astype(np.uint8),
        np.clip(g, 0, 255).astype(np.uint8),
        np.clip(b, 0, 255).astype(np.uint8),
    )


def colorize_velocity(
    velocity_m_yr: np.ndarray,
    valid_mask: np.ndarray,
    clip_abs_mm_yr: float,
) -> np.ndarray:
    """Colorize velocity raster to RGBA.

    Args:
        velocity_m_yr: Velocity in m/yr.
        valid_mask: Boolean mask of valid pixels.
        clip_abs_mm_yr: Symmetric color clip in mm/yr.

    Returns:
        RGBA array with shape (4, rows, cols), dtype uint8.
    """
    vel_mm_yr = velocity_m_yr * 1000.0
    r, g, b = velocity_to_rgb(vel_mm_yr, clip_abs_mm_yr=clip_abs_mm_yr)

    rgba = np.zeros((4, velocity_m_yr.shape[0], velocity_m_yr.shape[1]), dtype=np.uint8)
    rgba[0] = r
    rgba[1] = g
    rgba[2] = b
    rgba[3] = np.where(valid_mask, 255, 0).astype(np.uint8)
    return rgba


def write_colorized_geotiff(rgba: np.ndarray, out_tif: Path, profile: dict[str, Any]) -> None:
    """Write colorized RGBA GeoTIFF."""
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    out_profile = profile.copy()
    out_profile.update(
        {
            "driver": "GTiff",
            "count": 4,
            "dtype": "uint8",
            "nodata": None,
            "compress": "deflate",
            "predictor": 2,
            "photometric": "RGB",
        }
    )
    with rasterio.open(out_tif, "w", **out_profile) as ds:
        ds.write(rgba)
        ds.set_band_description(1, "red")
        ds.set_band_description(2, "green")
        ds.set_band_description(3, "blue")
        ds.set_band_description(4, "alpha")


def write_rgba_png(rgba: np.ndarray, out_png: Path) -> None:
    """Write RGBA array to PNG."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    _, rows, cols = rgba.shape
    with warnings.catch_warnings():
        # PNG assets inside KMZ are expected to be non-georeferenced.
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(
            out_png,
            "w",
            driver="PNG",
            width=cols,
            height=rows,
            count=4,
            dtype="uint8",
        ) as ds:
            ds.write(rgba)


def reproject_rgba_to_wgs84(
    rgba: np.ndarray,
    src_crs: Any,
    src_transform: Any,
    src_width: int,
    src_height: int,
    src_bounds: tuple[float, float, float, float],
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Reproject RGBA raster to EPSG:4326 for KML ground overlay.

    Args:
        rgba: Source RGBA array (4, rows, cols).
        src_crs: Source CRS.
        src_transform: Source affine transform.
        src_width: Source width.
        src_height: Source height.
        src_bounds: Source bounds (left, bottom, right, top).

    Returns:
        Tuple of (RGBA in EPSG:4326, bounds in WGS84 as west,south,east,north).
    """
    if src_crs is None:
        raise RuntimeError("Velocity raster CRS is missing; KMZ overlay requires georeferencing.")

    if str(src_crs).upper() in {"EPSG:4326", "OGC:CRS84"}:
        west, south, east, north = src_bounds
        return rgba, (west, south, east, north)

    dst_transform, dst_width, dst_height = calculate_default_transform(
        src_crs,
        "EPSG:4326",
        src_width,
        src_height,
        *src_bounds,
    )
    dst = np.zeros((4, dst_height, dst_width), dtype=np.uint8)
    for idx in range(4):
        reproject(
            source=rgba[idx],
            destination=dst[idx],
            src_transform=src_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs="EPSG:4326",
            resampling=Resampling.nearest if idx == 3 else Resampling.bilinear,
        )

    west, south, east, north = array_bounds(dst_height, dst_width, dst_transform)
    return dst, (west, south, east, north)


def build_legend_rgba(width: int, height: int, clip_abs_mm_yr: float) -> np.ndarray:
    """Build a simple horizontal legend gradient (blue-white-red)."""
    width = max(64, int(width))
    height = max(16, int(height))
    vals = np.linspace(-clip_abs_mm_yr, clip_abs_mm_yr, width, dtype=np.float32)
    vals2d = np.repeat(vals[np.newaxis, :], height, axis=0)
    r, g, b = velocity_to_rgb(vals2d, clip_abs_mm_yr)
    rgba = np.zeros((4, height, width), dtype=np.uint8)
    rgba[0] = r
    rgba[1] = g
    rgba[2] = b
    rgba[3] = 255
    return rgba


def build_kml(
    overlay_png_name: str,
    legend_png_name: str,
    west: float,
    south: float,
    east: float,
    north: float,
    name_prefix: str,
    clip_abs_mm_yr: float,
) -> str:
    """Create KML document for ground overlay + legend screen overlay."""
    description = (
        f"Velocity quicklook. Blue = negative, red = positive. "
        f"Color clip: +/-{clip_abs_mm_yr:.2f} mm/yr."
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
        "<Document>\n"
        f"<name>{name_prefix} velocity overlay</name>\n"
        f"<description>{description}</description>\n"
        "<GroundOverlay>\n"
        f"  <name>{name_prefix} velocity</name>\n"
        "  <Icon>\n"
        f"    <href>{overlay_png_name}</href>\n"
        "  </Icon>\n"
        "  <LatLonBox>\n"
        f"    <north>{north:.12f}</north>\n"
        f"    <south>{south:.12f}</south>\n"
        f"    <east>{east:.12f}</east>\n"
        f"    <west>{west:.12f}</west>\n"
        "    <rotation>0</rotation>\n"
        "  </LatLonBox>\n"
        "</GroundOverlay>\n"
        "<ScreenOverlay>\n"
        "  <name>Velocity legend</name>\n"
        "  <Icon>\n"
        f"    <href>{legend_png_name}</href>\n"
        "  </Icon>\n"
        '  <overlayXY x="0.02" y="0.02" xunits="fraction" yunits="fraction"/>\n'
        '  <screenXY x="0.02" y="0.02" xunits="fraction" yunits="fraction"/>\n'
        '  <size x="0.25" y="0" xunits="fraction" yunits="pixels"/>\n'
        "</ScreenOverlay>\n"
        "</Document>\n"
        "</kml>\n"
    )


def write_kmz_overlay(
    overlay_rgba_wgs84: np.ndarray,
    bounds_wgs84: tuple[float, float, float, float],
    out_kmz: Path,
    name_prefix: str,
    clip_abs_mm_yr: float,
    legend_width_px: int,
    legend_height_px: int,
) -> None:
    """Write KMZ with raster ground overlay and legend."""
    out_kmz.parent.mkdir(parents=True, exist_ok=True)
    west, south, east, north = bounds_wgs84

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        overlay_png = tmp_dir / "velocity_overlay.png"
        legend_png = tmp_dir / "velocity_legend.png"
        kml_path = tmp_dir / "doc.kml"

        write_rgba_png(overlay_rgba_wgs84, overlay_png)
        legend_rgba = build_legend_rgba(
            width=legend_width_px,
            height=legend_height_px,
            clip_abs_mm_yr=clip_abs_mm_yr,
        )
        write_rgba_png(legend_rgba, legend_png)

        kml_text = build_kml(
            overlay_png_name=overlay_png.name,
            legend_png_name=legend_png.name,
            west=west,
            south=south,
            east=east,
            north=north,
            name_prefix=name_prefix,
            clip_abs_mm_yr=clip_abs_mm_yr,
        )
        kml_path.write_text(kml_text, encoding="utf-8")

        with zipfile.ZipFile(out_kmz, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(kml_path, arcname="doc.kml")
            zf.write(overlay_png, arcname=overlay_png.name)
            zf.write(legend_png, arcname=legend_png.name)


def main() -> int:
    """Parse CLI args and export raster visualization products."""
    parser = argparse.ArgumentParser(
        description="Export Dolphin velocity raster to colorized GeoTIFF and KMZ overlay."
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
    viz_cfg = dolphin_cfg.get("raster_viz", {})

    enabled = bool_cfg(viz_cfg, "enabled", False)
    if not enabled:
        print("Raster viz export disabled (processing.dolphin.raster_viz.enabled=false).")
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
        str_cfg(viz_cfg, "output_dir", str(work_dir / "exports")),
    )
    velocity_file = resolve_path(
        repo_root,
        str_cfg(viz_cfg, "velocity_file", str(work_dir / "timeseries" / "velocity.tif")),
    )
    geotiff_enabled = bool_cfg(viz_cfg, "geotiff_enabled", True)
    kmz_enabled = bool_cfg(viz_cfg, "kmz_enabled", True)
    clip_abs_mm_yr = float_cfg(viz_cfg, "clip_abs_mm_per_year", 30.0)
    name_prefix = str_cfg(viz_cfg, "name_prefix", "dolphin_velocity")
    legend_width_px = int_cfg(viz_cfg, "legend_width_px", 512)
    legend_height_px = int_cfg(viz_cfg, "legend_height_px", 40)

    colorized_tif = resolve_path(
        repo_root,
        str_cfg(viz_cfg, "colorized_tif", str(output_dir / "velocity_colorized.tif")),
    )
    kmz_file = resolve_path(
        repo_root,
        str_cfg(viz_cfg, "kmz_file", str(output_dir / "velocity_overlay.kmz")),
    )

    if not geotiff_enabled and not kmz_enabled:
        print("Both geotiff_enabled and kmz_enabled are false; nothing to export.")
        return 0
    if not velocity_file.exists():
        print(f"Missing velocity raster: {velocity_file}")
        print("Run Dolphin first or set processing.dolphin.raster_viz.velocity_file.")
        return 2

    print(f"Config: {stack_config}")
    print(f"Velocity file: {velocity_file}")
    print(f"Output dir: {output_dir}")
    print(f"GeoTIFF enabled: {geotiff_enabled} -> {colorized_tif}")
    print(f"KMZ enabled: {kmz_enabled} -> {kmz_file}")
    print(f"Clip abs (mm/yr): {clip_abs_mm_yr}")
    print(f"Legend size (px): {legend_width_px}x{legend_height_px}")

    if args.dry_run:
        return 0

    with rasterio.open(velocity_file) as ds:
        vel = ds.read(1).astype(np.float32)
        nodata = ds.nodata
        valid = np.isfinite(vel)
        if nodata is not None:
            valid &= vel != nodata

        rgba = colorize_velocity(
            velocity_m_yr=vel,
            valid_mask=valid,
            clip_abs_mm_yr=clip_abs_mm_yr,
        )

        if geotiff_enabled:
            write_colorized_geotiff(rgba, colorized_tif, ds.profile)

        bounds = (ds.bounds.left, ds.bounds.bottom, ds.bounds.right, ds.bounds.top)
        if kmz_enabled:
            rgba_wgs84, wgs84_bounds = reproject_rgba_to_wgs84(
                rgba=rgba,
                src_crs=ds.crs,
                src_transform=ds.transform,
                src_width=ds.width,
                src_height=ds.height,
                src_bounds=bounds,
            )
            write_kmz_overlay(
                overlay_rgba_wgs84=rgba_wgs84,
                bounds_wgs84=wgs84_bounds,
                out_kmz=kmz_file,
                name_prefix=name_prefix,
                clip_abs_mm_yr=clip_abs_mm_yr,
                legend_width_px=legend_width_px,
                legend_height_px=legend_height_px,
            )

    summary = {
        "exported_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(stack_config),
        "velocity_file": str(velocity_file),
        "clip_abs_mm_per_year": clip_abs_mm_yr,
        "geotiff_enabled": geotiff_enabled,
        "kmz_enabled": kmz_enabled,
        "colorized_tif": str(colorized_tif) if geotiff_enabled else None,
        "kmz_file": str(kmz_file) if kmz_enabled else None,
        "name_prefix": name_prefix,
        "legend_width_px": legend_width_px,
        "legend_height_px": legend_height_px,
    }
    summary_path = output_dir / "raster_viz_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_summary = summary_path.with_suffix(summary_path.suffix + ".tmp")
    tmp_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    os.replace(tmp_summary, summary_path)

    print(f"Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
