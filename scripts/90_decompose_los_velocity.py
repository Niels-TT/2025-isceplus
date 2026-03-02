#!/usr/bin/env python3
"""Decompose ascending/descending LOS velocity rasters into East/Up components.

Technical summary:
    Loads two LOS velocity products (ascending + descending), aligns them to a
    common grid, solves a 2x2 system per pixel using manual or auto-derived LOS
    projection coefficients, and writes East/Up rasters plus QC artifacts.

Why:
    Adds an explicit decomposition stage on top of the existing
    COMPASS -> Dolphin pipeline while keeping raster outputs as the primary
    scientific products.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from tqdm import tqdm


@dataclass(frozen=True)
class TrackConfig:
    """Input and geometry config for one LOS track."""

    name: str
    velocity_file: Path
    coherence_file: Path | None
    los_east_coeff: float
    los_up_coeff: float
    los_coeff_source: str
    los_coeff_details: dict[str, Any] | None = None


@dataclass(frozen=True)
class DecompositionConfig:
    """Resolved decomposition configuration."""

    enabled: bool
    asc: TrackConfig
    dsc: TrackConfig
    output_dir: Path
    target_grid: str
    min_temporal_coherence: float
    max_condition_number: float
    write_consistency_error: bool
    velocity_resampling: Resampling
    coherence_resampling: Resampling
    point_exports: "DecompositionPointExportConfig"
    raster_viz: "DecompositionRasterVizConfig"


@dataclass(frozen=True)
class DecompositionPointExportConfig:
    """Optional decomposition point export settings."""

    enabled: bool
    csv_enabled: bool
    kmz_enabled: bool
    output_dir: Path
    name_prefix: str
    stride: int
    max_points: int
    altitude_scale_m_per_mm_per_year: float
    color_clip_abs_mm_per_year: float
    kmz_compact_mode: bool
    kmz_compact_color_bins: int
    kmz_compact_max_points_per_placemark: int
    kmz_use_network_links: bool
    kmz_region_target_points: int
    kmz_region_min_lod_pixels: int
    east_csv_file: Path
    up_csv_file: Path
    east_kmz_file: Path
    up_kmz_file: Path


@dataclass(frozen=True)
class DecompositionRasterVizConfig:
    """Optional decomposition raster visualization export settings."""

    enabled: bool
    geotiff_enabled: bool
    kmz_enabled: bool
    output_dir: Path
    name_prefix: str
    clip_abs_mm_per_year: float
    legend_width_px: int
    legend_height_px: int
    east_colorized_tif: Path
    up_colorized_tif: Path
    east_kmz_file: Path
    up_kmz_file: Path


def read_toml(path: Path) -> dict[str, Any]:
    """Read TOML file from disk."""
    with path.open("rb") as f:
        return tomllib.load(f)


def resolve_path(repo_root: Path, value: str) -> Path:
    """Resolve a path relative to repository root when needed."""
    p = Path(value)
    return p if p.is_absolute() else (repo_root / p).resolve()


def str_cfg(cfg: dict[str, Any], key: str, default: str) -> str:
    """Read non-empty string config value with fallback."""
    val = cfg.get(key, default)
    text = str(val).strip() if val is not None else ""
    return text if text else default


def float_cfg(cfg: dict[str, Any], key: str, default: float) -> float:
    """Read float config value with fallback."""
    val = cfg.get(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def int_cfg(cfg: dict[str, Any], key: str, default: int) -> int:
    """Read integer config value with fallback."""
    val = cfg.get(key, default)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def bool_cfg(cfg: dict[str, Any], key: str, default: bool) -> bool:
    """Read bool-like config value with fallback."""
    val = cfg.get(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        v = val.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    return bool(val)


def parse_resampling(name: str, default: Resampling) -> Resampling:
    """Parse rasterio resampling enum from config text."""
    if not name:
        return default
    value = name.strip().lower()
    mapping = {
        "nearest": Resampling.nearest,
        "bilinear": Resampling.bilinear,
        "cubic": Resampling.cubic,
        "average": Resampling.average,
    }
    if value not in mapping:
        allowed = ", ".join(sorted(mapping))
        raise ValueError(f"Unsupported resampling '{name}'. Allowed: {allowed}")
    return mapping[value]


def parse_point_exports_config(
    *,
    repo_root: Path,
    decomp_cfg: dict[str, Any],
    output_dir: Path,
) -> DecompositionPointExportConfig:
    """Parse decomposition point export config."""
    section = decomp_cfg.get("point_exports", {})
    if not isinstance(section, dict):
        section = {}

    enabled = bool_cfg(section, "enabled", False)
    csv_enabled = bool_cfg(section, "csv_enabled", True)
    kmz_enabled = bool_cfg(section, "kmz_enabled", True)

    exports_dir = resolve_path(
        repo_root,
        str_cfg(section, "output_dir", str(output_dir / "exports")),
    )
    name_prefix = str_cfg(section, "name_prefix", "decomposition")
    stride = max(1, int_cfg(section, "stride", 1))
    max_points = int_cfg(section, "max_points", 80000)
    altitude_scale = float_cfg(section, "altitude_scale_m_per_mm_per_year", 3.0)
    color_clip = float_cfg(section, "color_clip_abs_mm_per_year", 10.0)

    kmz_compact_mode = bool_cfg(section, "kmz_compact_mode", True)
    kmz_compact_color_bins = max(1, int_cfg(section, "kmz_compact_color_bins", 32))
    kmz_compact_max_points = max(
        1, int_cfg(section, "kmz_compact_max_points_per_placemark", 5000)
    )
    kmz_use_network_links = bool_cfg(section, "kmz_use_network_links", True)
    kmz_region_target_points = max(1, int_cfg(section, "kmz_region_target_points", 2000))
    kmz_region_min_lod_pixels = max(0, int_cfg(section, "kmz_region_min_lod_pixels", 0))

    east_csv_file = resolve_path(
        repo_root,
        str_cfg(
            section,
            "east_csv_file",
            str(exports_dir / "east_velocity_points.csv"),
        ),
    )
    up_csv_file = resolve_path(
        repo_root,
        str_cfg(
            section,
            "up_csv_file",
            str(exports_dir / "up_velocity_points.csv"),
        ),
    )
    east_kmz_file = resolve_path(
        repo_root,
        str_cfg(
            section,
            "east_kmz_file",
            str(exports_dir / "east_velocity_points.kmz"),
        ),
    )
    up_kmz_file = resolve_path(
        repo_root,
        str_cfg(
            section,
            "up_kmz_file",
            str(exports_dir / "up_velocity_points.kmz"),
        ),
    )

    return DecompositionPointExportConfig(
        enabled=enabled,
        csv_enabled=csv_enabled,
        kmz_enabled=kmz_enabled,
        output_dir=exports_dir,
        name_prefix=name_prefix,
        stride=stride,
        max_points=max_points,
        altitude_scale_m_per_mm_per_year=altitude_scale,
        color_clip_abs_mm_per_year=color_clip,
        kmz_compact_mode=kmz_compact_mode,
        kmz_compact_color_bins=kmz_compact_color_bins,
        kmz_compact_max_points_per_placemark=kmz_compact_max_points,
        kmz_use_network_links=kmz_use_network_links,
        kmz_region_target_points=kmz_region_target_points,
        kmz_region_min_lod_pixels=kmz_region_min_lod_pixels,
        east_csv_file=east_csv_file,
        up_csv_file=up_csv_file,
        east_kmz_file=east_kmz_file,
        up_kmz_file=up_kmz_file,
    )


def parse_raster_viz_config(
    *,
    repo_root: Path,
    decomp_cfg: dict[str, Any],
    output_dir: Path,
) -> DecompositionRasterVizConfig:
    """Parse decomposition raster visualization config."""
    section = decomp_cfg.get("raster_viz", {})
    if not isinstance(section, dict):
        section = {}

    enabled = bool_cfg(section, "enabled", False)
    geotiff_enabled = bool_cfg(section, "geotiff_enabled", True)
    kmz_enabled = bool_cfg(section, "kmz_enabled", True)

    exports_dir = resolve_path(
        repo_root,
        str_cfg(section, "output_dir", str(output_dir / "exports")),
    )
    name_prefix = str_cfg(section, "name_prefix", "decomposition")
    clip_abs_mm_per_year = float_cfg(section, "clip_abs_mm_per_year", 10.0)
    legend_width_px = max(64, int_cfg(section, "legend_width_px", 512))
    legend_height_px = max(16, int_cfg(section, "legend_height_px", 40))

    east_colorized_tif = resolve_path(
        repo_root,
        str_cfg(
            section,
            "east_colorized_tif",
            str(exports_dir / "east_velocity_colorized.tif"),
        ),
    )
    up_colorized_tif = resolve_path(
        repo_root,
        str_cfg(
            section,
            "up_colorized_tif",
            str(exports_dir / "up_velocity_colorized.tif"),
        ),
    )
    east_kmz_file = resolve_path(
        repo_root,
        str_cfg(
            section,
            "east_kmz_file",
            str(exports_dir / "east_velocity_overlay.kmz"),
        ),
    )
    up_kmz_file = resolve_path(
        repo_root,
        str_cfg(
            section,
            "up_kmz_file",
            str(exports_dir / "up_velocity_overlay.kmz"),
        ),
    )

    return DecompositionRasterVizConfig(
        enabled=enabled,
        geotiff_enabled=geotiff_enabled,
        kmz_enabled=kmz_enabled,
        output_dir=exports_dir,
        name_prefix=name_prefix,
        clip_abs_mm_per_year=clip_abs_mm_per_year,
        legend_width_px=legend_width_px,
        legend_height_px=legend_height_px,
        east_colorized_tif=east_colorized_tif,
        up_colorized_tif=up_colorized_tif,
        east_kmz_file=east_kmz_file,
        up_kmz_file=up_kmz_file,
    )


def parse_track(
    *,
    repo_root: Path,
    section: dict[str, Any],
    section_name: str,
) -> TrackConfig:
    """Parse and validate one track section."""
    name = str_cfg(section, "name", section_name)

    velocity_value = str_cfg(section, "velocity_file", "")
    if not velocity_value:
        raise ValueError(f"processing.decomposition.track_{section_name}.velocity_file is required.")
    velocity_file = resolve_path(repo_root, velocity_value)
    if not velocity_file.exists():
        raise FileNotFoundError(f"Missing velocity raster: {velocity_file}")

    coherence_value = str_cfg(section, "coherence_file", "")
    coherence_file = resolve_path(repo_root, coherence_value) if coherence_value else None
    if coherence_file is not None and not coherence_file.exists():
        raise FileNotFoundError(f"Missing coherence raster: {coherence_file}")

    los_east_coeff, los_up_coeff, source, details = resolve_los_coefficients(
        repo_root=repo_root,
        section=section,
        section_name=section_name,
        velocity_file=velocity_file,
    )

    return TrackConfig(
        name=name,
        velocity_file=velocity_file,
        coherence_file=coherence_file,
        los_east_coeff=los_east_coeff,
        los_up_coeff=los_up_coeff,
        los_coeff_source=source,
        los_coeff_details=details,
    )


def resolve_los_coefficients(
    *,
    repo_root: Path,
    section: dict[str, Any],
    section_name: str,
    velocity_file: Path,
) -> tuple[float, float, str, dict[str, Any] | None]:
    """Resolve LOS coefficients from config or auto-derive from COMPASS geometry."""
    has_east = "los_east_coeff" in section
    has_up = "los_up_coeff" in section

    if has_east != has_up:
        raise ValueError(
            f"processing.decomposition.track_{section_name} must provide both "
            "los_east_coeff and los_up_coeff, or omit both for auto mode."
        )

    if has_east and has_up:
        east_raw = section["los_east_coeff"]
        up_raw = section["los_up_coeff"]

        east_auto = isinstance(east_raw, str) and east_raw.strip().lower() == "auto"
        up_auto = isinstance(up_raw, str) and up_raw.strip().lower() == "auto"
        if east_auto or up_auto:
            if not (east_auto and up_auto):
                raise ValueError(
                    f"processing.decomposition.track_{section_name} must set both "
                    "los_east_coeff and los_up_coeff to 'auto' when using auto mode."
                )
            return estimate_los_coefficients_from_compass_geometry(
                repo_root=repo_root,
                section_name=section_name,
                velocity_file=velocity_file,
            )

        try:
            east = float(east_raw)
            up = float(up_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"processing.decomposition.track_{section_name} has invalid LOS coefficients. "
                "Use numeric values or set both to 'auto'."
            ) from exc
        return east, up, "manual", None

    # Auto mode when both keys are omitted.
    return estimate_los_coefficients_from_compass_geometry(
        repo_root=repo_root,
        section_name=section_name,
        velocity_file=velocity_file,
    )


def estimate_los_coefficients_from_compass_geometry(
    *,
    repo_root: Path,
    section_name: str,
    velocity_file: Path,
) -> tuple[float, float, str, dict[str, Any]]:
    """Estimate LOS East/Up coefficients from COMPASS scratch geometry rasters.

    The formulas follow COMPASS/MintPy ENU->LOS convention:
      v_los = v_e * (-sin(inc)*sin(az)) + v_n*(sin(inc)*cos(az)) + v_u*cos(inc)
    where az is LOS azimuth angle (degrees) measured from north with
    anti-clockwise positive. The heading rasters produced by COMPASS rdr2geo
    provide this LOS azimuth convention.
    """
    stack_root = infer_stack_root_from_velocity(velocity_file)
    scratch_dir = stack_root / "stack" / "compass" / "scratch"
    if not scratch_dir.exists():
        raise FileNotFoundError(
            f"processing.decomposition.track_{section_name}: auto LOS coefficient mode "
            f"requires COMPASS geometry under {scratch_dir}. "
            "Run COMPASS first, or set manual los_east_coeff/los_up_coeff."
        )

    all_pairs = list_compass_geometry_pairs(scratch_dir)
    if not all_pairs:
        raise FileNotFoundError(
            f"processing.decomposition.track_{section_name}: auto LOS coefficient mode found "
            f"no geometry pairs under {scratch_dir}. "
            "Expected corrections/incidence_angle.tif + heading_angle.tif files."
        )

    sampled_pairs = sample_pairs_evenly(all_pairs, max_samples=24)
    incidence_medians: list[float] = []
    azimuth_medians: list[float] = []
    for inc_path, az_path in sampled_pairs:
        incidence_medians.append(read_raster_median(inc_path))
        azimuth_medians.append(read_raster_median(az_path))

    incidence_deg = float(np.nanmedian(np.asarray(incidence_medians, dtype=np.float64)))
    azimuth_deg = circular_mean_deg(azimuth_medians)

    inc_rad = np.deg2rad(incidence_deg)
    az_rad = np.deg2rad(azimuth_deg)
    east = float(-np.sin(inc_rad) * np.sin(az_rad))
    north = float(np.sin(inc_rad) * np.cos(az_rad))
    up = float(np.cos(inc_rad))

    details: dict[str, Any] = {
        "mode": "auto_from_compass_geometry",
        "track": section_name,
        "repo_root": str(repo_root),
        "stack_root": str(stack_root),
        "scratch_dir": str(scratch_dir),
        "file_pair_count_total": len(all_pairs),
        "file_pair_count_sampled": len(sampled_pairs),
        "incidence_median_deg": incidence_deg,
        "los_azimuth_median_deg": azimuth_deg,
        "north_coeff_diagnostic": north,
        "sample_incidence_files": [str(p[0]) for p in sampled_pairs[:5]],
        "sample_azimuth_files": [str(p[1]) for p in sampled_pairs[:5]],
    }
    return east, up, "auto_from_compass_geometry", details


def infer_stack_root_from_velocity(velocity_file: Path) -> Path:
    """Infer stack root path from the Dolphin velocity raster path."""
    v = velocity_file.resolve()

    # Canonical stack layout: <stack_root>/stack/dolphin/timeseries/velocity.tif
    if (
        v.name == "velocity.tif"
        and v.parent.name == "timeseries"
        and v.parent.parent.name == "dolphin"
        and v.parent.parent.parent.name == "stack"
    ):
        return v.parent.parent.parent.parent

    # Fallback: find any ancestor that contains stack/compass/scratch.
    for ancestor in v.parents:
        if (ancestor / "stack" / "compass" / "scratch").exists():
            return ancestor

    raise ValueError(
        "Unable to infer stack root from velocity raster path. "
        f"Expected .../stack/dolphin/timeseries/velocity.tif, got {v}"
    )


def list_compass_geometry_pairs(scratch_dir: Path) -> list[tuple[Path, Path]]:
    """List incidence/azimuth geometry raster pairs from COMPASS scratch directory."""
    pairs: list[tuple[Path, Path]] = []
    for inc in sorted(scratch_dir.rglob("corrections/incidence_angle.tif")):
        az = inc.with_name("heading_angle.tif")
        if az.exists():
            pairs.append((inc, az))
    return pairs


def sample_pairs_evenly(
    pairs: list[tuple[Path, Path]],
    *,
    max_samples: int,
) -> list[tuple[Path, Path]]:
    """Downsample file pairs while preserving temporal/burst spread."""
    if len(pairs) <= max_samples:
        return pairs
    idx = np.linspace(0, len(pairs) - 1, num=max_samples, dtype=np.float64)
    keep = sorted({int(round(i)) for i in idx})
    return [pairs[i] for i in keep]


def read_raster_median(path: Path) -> float:
    """Read one-band raster and return median over finite, non-nodata pixels."""
    with rasterio.open(path) as ds:
        arr = ds.read(1)
        nodata = ds.nodata
    valid = np.isfinite(arr)
    if nodata is not None and np.isfinite(nodata):
        valid &= arr != nodata
    if not np.any(valid):
        raise ValueError(f"Geometry raster has no valid values: {path}")
    return float(np.nanmedian(arr[valid]))


def circular_mean_deg(values: list[float]) -> float:
    """Compute circular mean for angles in degrees."""
    if not values:
        raise ValueError("Cannot compute circular mean of empty angle list.")
    rad = np.deg2rad(np.asarray(values, dtype=np.float64))
    s = float(np.mean(np.sin(rad)))
    c = float(np.mean(np.cos(rad)))
    return float(np.rad2deg(np.arctan2(s, c)))


def parse_config(repo_root: Path, config_path: Path) -> DecompositionConfig:
    """Parse decomposition config from stack TOML.

    Supports either:
    - `[processing.decomposition]` (preferred inside processing configuration),
    - or legacy top-level `[decomposition]`.
    """
    cfg = read_toml(config_path)

    processing = cfg.get("processing", {})
    if isinstance(processing, dict) and isinstance(processing.get("decomposition"), dict):
        decomp = processing["decomposition"]
    else:
        decomp = cfg.get("decomposition", {})

    if not isinstance(decomp, dict) or not decomp:
        raise ValueError("Missing [processing.decomposition] (or legacy [decomposition]) table.")

    enabled = bool_cfg(decomp, "enabled", True)

    track_asc = decomp.get("track_asc")
    track_dsc = decomp.get("track_dsc")
    if not isinstance(track_asc, dict) or not isinstance(track_dsc, dict):
        raise ValueError(
            "Missing [processing.decomposition.track_asc] or "
            "[processing.decomposition.track_dsc] table."
        )

    asc = parse_track(repo_root=repo_root, section=track_asc, section_name="asc")
    dsc = parse_track(repo_root=repo_root, section=track_dsc, section_name="dsc")

    output_dir_value = str_cfg(decomp, "output_dir", "")
    if not output_dir_value:
        raise ValueError("processing.decomposition.output_dir is required.")
    output_dir = resolve_path(repo_root, output_dir_value)

    target_grid = str_cfg(decomp, "target_grid", "asc").lower()
    if target_grid not in {"asc", "dsc"}:
        raise ValueError("processing.decomposition.target_grid must be 'asc' or 'dsc'.")

    min_temporal_coherence = float_cfg(decomp, "min_temporal_coherence", 0.0)
    if min_temporal_coherence < 0.0 or min_temporal_coherence > 1.0:
        raise ValueError("processing.decomposition.min_temporal_coherence must be in [0, 1].")

    max_condition_number = float_cfg(decomp, "max_condition_number", 50.0)
    if max_condition_number <= 0:
        raise ValueError("processing.decomposition.max_condition_number must be > 0.")

    write_consistency_error = bool_cfg(decomp, "write_consistency_error", True)
    velocity_resampling = parse_resampling(
        str_cfg(decomp, "velocity_resampling", "bilinear"),
        default=Resampling.bilinear,
    )
    coherence_resampling = parse_resampling(
        str_cfg(decomp, "coherence_resampling", "bilinear"),
        default=Resampling.bilinear,
    )
    point_exports = parse_point_exports_config(
        repo_root=repo_root,
        decomp_cfg=decomp,
        output_dir=output_dir,
    )
    raster_viz = parse_raster_viz_config(
        repo_root=repo_root,
        decomp_cfg=decomp,
        output_dir=output_dir,
    )

    return DecompositionConfig(
        enabled=enabled,
        asc=asc,
        dsc=dsc,
        output_dir=output_dir,
        target_grid=target_grid,
        min_temporal_coherence=min_temporal_coherence,
        max_condition_number=max_condition_number,
        write_consistency_error=write_consistency_error,
        velocity_resampling=velocity_resampling,
        coherence_resampling=coherence_resampling,
        point_exports=point_exports,
        raster_viz=raster_viz,
    )


def matrix_diagnostics(
    asc_east: float,
    asc_up: float,
    dsc_east: float,
    dsc_up: float,
) -> tuple[np.ndarray, float, float]:
    """Build decomposition matrix and return diagnostics.

    Returns:
        Inverse matrix, determinant, and condition number.
    """
    a = np.array([[asc_east, asc_up], [dsc_east, dsc_up]], dtype=np.float64)
    det = float(np.linalg.det(a))
    if abs(det) < 1e-12:
        raise ValueError("Decomposition matrix is singular; adjust LOS coefficients.")
    cond = float(np.linalg.cond(a))
    inv = np.linalg.inv(a)
    return inv, det, cond


def valid_data_mask(arr: np.ndarray, nodata: float | None) -> np.ndarray:
    """Build valid-data mask for one raster array."""
    valid = np.isfinite(arr)
    if nodata is not None and np.isfinite(nodata):
        valid &= arr != nodata
    return valid


def build_float_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Build float32 output profile for GeoTIFF outputs."""
    out = profile.copy()
    out.update(
        {
            "driver": "GTiff",
            "dtype": "float32",
            "count": 1,
            "nodata": np.nan,
            "compress": "deflate",
            "predictor": 3,
            "zlevel": 4,
        }
    )
    return out


def build_uint8_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Build uint8 output profile for binary mask outputs."""
    out = profile.copy()
    out.update(
        {
            "driver": "GTiff",
            "dtype": "uint8",
            "count": 1,
            "nodata": 0,
            "compress": "deflate",
            "predictor": 2,
            "zlevel": 4,
        }
    )
    return out


_HELPER_MODULE_CACHE: dict[str, Any] = {}


def load_script_module(script_name: str, module_name: str) -> Any:
    """Dynamically load a helper module from scripts/."""
    cache_key = f"{script_name}:{module_name}"
    cached = _HELPER_MODULE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    script_path = Path(__file__).with_name(script_name)
    if not script_path.exists():
        raise FileNotFoundError(f"Missing helper script: {script_path}")

    script_dir = str(script_path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _HELPER_MODULE_CACHE[cache_key] = module
    return module


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically to avoid partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def run_raster_viz_exports(
    *,
    cfg: DecompositionRasterVizConfig,
    east_file: Path,
    up_file: Path,
    config_path: Path,
) -> dict[str, Any]:
    """Export decomposition East/Up rasters to colorized GeoTIFF and KMZ."""
    if not cfg.enabled:
        return {"enabled": False}
    if not cfg.geotiff_enabled and not cfg.kmz_enabled:
        return {
            "enabled": True,
            "geotiff_enabled": False,
            "kmz_enabled": False,
            "reason": "both_outputs_disabled",
        }

    helper = load_script_module(
        "13_export_dolphin_raster_viz.py",
        "_decomposition_raster_viz_helper",
    )
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    components = [
        ("east", east_file, cfg.east_colorized_tif, cfg.east_kmz_file),
        ("up", up_file, cfg.up_colorized_tif, cfg.up_kmz_file),
    ]
    component_results: dict[str, Any] = {}

    for comp, velocity_file, colorized_tif, kmz_file in components:
        if not velocity_file.exists():
            raise FileNotFoundError(f"Missing decomposition {comp} raster: {velocity_file}")

        print(
            f"Decomposition raster viz ({comp}): "
            f"geotiff={cfg.geotiff_enabled} -> {colorized_tif}, "
            f"kmz={cfg.kmz_enabled} -> {kmz_file}"
        )

        with rasterio.open(velocity_file) as ds:
            vel = ds.read(1).astype(np.float32)
            nodata = ds.nodata
            valid = np.isfinite(vel)
            if nodata is not None and np.isfinite(nodata):
                valid &= vel != nodata

            rgba = helper.colorize_velocity(
                velocity_m_yr=vel,
                valid_mask=valid,
                clip_abs_mm_yr=cfg.clip_abs_mm_per_year,
            )

            if cfg.geotiff_enabled:
                helper.write_colorized_geotiff(rgba, colorized_tif, ds.profile)

            if cfg.kmz_enabled:
                src_bounds = (ds.bounds.left, ds.bounds.bottom, ds.bounds.right, ds.bounds.top)
                rgba_wgs84, wgs84_bounds = helper.reproject_rgba_to_wgs84(
                    rgba=rgba,
                    src_crs=ds.crs,
                    src_transform=ds.transform,
                    src_width=ds.width,
                    src_height=ds.height,
                    src_bounds=src_bounds,
                )
                helper.write_kmz_overlay(
                    overlay_rgba_wgs84=rgba_wgs84,
                    bounds_wgs84=wgs84_bounds,
                    out_kmz=kmz_file,
                    name_prefix=f"{cfg.name_prefix}_{comp}",
                    clip_abs_mm_yr=cfg.clip_abs_mm_per_year,
                    legend_width_px=cfg.legend_width_px,
                    legend_height_px=cfg.legend_height_px,
                )

        component_results[comp] = {
            "velocity_file": str(velocity_file),
            "colorized_tif": str(colorized_tif) if cfg.geotiff_enabled else None,
            "kmz_file": str(kmz_file) if cfg.kmz_enabled else None,
        }

    summary = {
        "exported_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "enabled": True,
        "geotiff_enabled": cfg.geotiff_enabled,
        "kmz_enabled": cfg.kmz_enabled,
        "clip_abs_mm_per_year": cfg.clip_abs_mm_per_year,
        "legend_width_px": cfg.legend_width_px,
        "legend_height_px": cfg.legend_height_px,
        "name_prefix": cfg.name_prefix,
        "components": component_results,
    }
    summary_path = cfg.output_dir / "decomposition_raster_viz_summary.json"
    write_json_atomic(summary_path, summary)
    print(f"Decomposition raster viz summary: {summary_path}")
    summary["summary_file"] = str(summary_path)
    return summary


def run_point_exports(
    *,
    cfg: DecompositionPointExportConfig,
    east_file: Path,
    up_file: Path,
    config_path: Path,
) -> dict[str, Any]:
    """Export decomposition East/Up rasters to CSV + KMZ point products."""
    if not cfg.enabled:
        return {"enabled": False}
    if not cfg.csv_enabled and not cfg.kmz_enabled:
        return {
            "enabled": True,
            "csv_enabled": False,
            "kmz_enabled": False,
            "reason": "both_outputs_disabled",
        }

    helper = load_script_module(
        "12_export_dolphin_points.py",
        "_decomposition_point_export_helper",
    )
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    components = [
        ("east", east_file, cfg.east_csv_file, cfg.east_kmz_file),
        ("up", up_file, cfg.up_csv_file, cfg.up_kmz_file),
    ]
    component_results: dict[str, Any] = {}

    for comp, velocity_file, csv_file, kmz_file in components:
        if not velocity_file.exists():
            raise FileNotFoundError(f"Missing decomposition {comp} raster: {velocity_file}")

        print(
            f"Decomposition point export ({comp}): "
            f"csv={cfg.csv_enabled} -> {csv_file}, "
            f"kmz={cfg.kmz_enabled} -> {kmz_file}"
        )

        points, stats = helper.select_points(
            velocity_file=velocity_file,
            coherence_file=None,
            ps_mask_file=None,
            min_temporal_coherence=0.0,
            use_ps_mask=False,
            stride=cfg.stride,
            max_points=cfg.max_points,
            strict_grid_match=False,
        )

        if cfg.csv_enabled:
            helper.write_csv(
                points,
                csv_file,
                altitude_scale=cfg.altitude_scale_m_per_mm_per_year,
            )
        if cfg.kmz_enabled:
            helper.write_kmz(
                points=points,
                out_kmz=kmz_file,
                altitude_scale=cfg.altitude_scale_m_per_mm_per_year,
                clip_abs_mm_yr=cfg.color_clip_abs_mm_per_year,
                name_prefix=f"{cfg.name_prefix}_{comp}",
                compact_mode=cfg.kmz_compact_mode,
                compact_color_bins=cfg.kmz_compact_color_bins,
                compact_max_points_per_placemark=cfg.kmz_compact_max_points_per_placemark,
                use_network_links=cfg.kmz_use_network_links,
                region_target_points=cfg.kmz_region_target_points,
                region_min_lod_pixels=cfg.kmz_region_min_lod_pixels,
            )

        component_results[comp] = {
            "velocity_file": str(velocity_file),
            "csv_file": str(csv_file) if cfg.csv_enabled else None,
            "kmz_file": str(kmz_file) if cfg.kmz_enabled else None,
            "stats": stats,
        }

    summary = {
        "exported_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "enabled": True,
        "csv_enabled": cfg.csv_enabled,
        "kmz_enabled": cfg.kmz_enabled,
        "stride": cfg.stride,
        "max_points": cfg.max_points,
        "altitude_scale_m_per_mm_per_year": cfg.altitude_scale_m_per_mm_per_year,
        "color_clip_abs_mm_per_year": cfg.color_clip_abs_mm_per_year,
        "kmz_compact_mode": cfg.kmz_compact_mode,
        "kmz_compact_color_bins": cfg.kmz_compact_color_bins,
        "kmz_compact_max_points_per_placemark": cfg.kmz_compact_max_points_per_placemark,
        "kmz_use_network_links": cfg.kmz_use_network_links,
        "kmz_region_target_points": cfg.kmz_region_target_points,
        "kmz_region_min_lod_pixels": cfg.kmz_region_min_lod_pixels,
        "name_prefix": cfg.name_prefix,
        "components": component_results,
    }
    summary_path = cfg.output_dir / "decomposition_point_export_summary.json"
    write_json_atomic(summary_path, summary)
    print(f"Decomposition point export summary: {summary_path}")
    summary["summary_file"] = str(summary_path)
    return summary


def run_decomposition(
    cfg: DecompositionConfig,
    config_path: Path,
    dry_run: bool = False,
) -> int:
    """Execute decomposition stage.

    Args:
        cfg: Parsed decomposition settings.
        config_path: Source TOML file path.
        dry_run: If True, validates and prints planned work only.

    Returns:
        Process exit code.
    """
    inv, determinant, condition_number = matrix_diagnostics(
        cfg.asc.los_east_coeff,
        cfg.asc.los_up_coeff,
        cfg.dsc.los_east_coeff,
        cfg.dsc.los_up_coeff,
    )

    print(f"Config: {config_path}")
    print(f"Enabled: {cfg.enabled}")
    print(f"ASC velocity: {cfg.asc.velocity_file}")
    print(f"DSC velocity: {cfg.dsc.velocity_file}")
    print(f"ASC coherence: {cfg.asc.coherence_file}")
    print(f"DSC coherence: {cfg.dsc.coherence_file}")
    print(
        "LOS coefficient source: "
        f"asc={cfg.asc.los_coeff_source}, dsc={cfg.dsc.los_coeff_source}"
    )
    print_track_coeff_details("ASC", cfg.asc)
    print_track_coeff_details("DSC", cfg.dsc)
    print(
        "LOS coefficients: "
        f"asc(e={cfg.asc.los_east_coeff:.6f}, u={cfg.asc.los_up_coeff:.6f}), "
        f"dsc(e={cfg.dsc.los_east_coeff:.6f}, u={cfg.dsc.los_up_coeff:.6f})"
    )
    print(f"Matrix determinant: {determinant:.6e}")
    print(f"Matrix condition number: {condition_number:.3f}")
    print(f"Condition threshold: {cfg.max_condition_number:.3f}")
    print(f"Target grid: {cfg.target_grid}")
    print(f"Min temporal coherence: {cfg.min_temporal_coherence:.3f}")
    print(f"Output dir: {cfg.output_dir}")

    if not cfg.enabled:
        print("Decomposition is disabled in config. Nothing to do.")
        return 0

    if condition_number > cfg.max_condition_number:
        print(
            "Condition number exceeds threshold; decomposition geometry is ill-conditioned.",
            file=sys.stderr,
        )
        return 2

    east_out = cfg.output_dir / "east_velocity_m_per_year.tif"
    up_out = cfg.output_dir / "up_velocity_m_per_year.tif"
    valid_out = cfg.output_dir / "valid_mask.tif"
    cond_out = cfg.output_dir / "condition_number.tif"
    consistency_out = cfg.output_dir / "consistency_error_m_per_year.tif"
    summary_out = cfg.output_dir / "decomposition_summary.json"

    print(f"Planned output east: {east_out}")
    print(f"Planned output up: {up_out}")
    if cfg.write_consistency_error:
        print(f"Planned output consistency error: {consistency_out}")
    if cfg.raster_viz.enabled:
        print(
            "Planned decomposition raster viz: "
            f"east_tif={cfg.raster_viz.east_colorized_tif}, "
            f"up_tif={cfg.raster_viz.up_colorized_tif}, "
            f"east_kmz={cfg.raster_viz.east_kmz_file}, "
            f"up_kmz={cfg.raster_viz.up_kmz_file}"
        )
    else:
        print("Planned decomposition raster viz: disabled")
    if cfg.point_exports.enabled:
        print(
            "Planned decomposition point exports: "
            f"east_csv={cfg.point_exports.east_csv_file}, "
            f"up_csv={cfg.point_exports.up_csv_file}, "
            f"east_kmz={cfg.point_exports.east_kmz_file}, "
            f"up_kmz={cfg.point_exports.up_kmz_file}"
        )
    else:
        print("Planned decomposition point exports: disabled")

    if dry_run:
        return 0

    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    total_pixels = 0
    valid_pixels = 0
    coherence_screened = 0
    nodata_screened = 0

    with contextlib.ExitStack() as stack:
        asc_ds = stack.enter_context(rasterio.open(cfg.asc.velocity_file))
        dsc_ds = stack.enter_context(rasterio.open(cfg.dsc.velocity_file))

        if cfg.target_grid == "asc":
            ref_ds = asc_ds
            other_ds = dsc_ds
            ref_track = cfg.asc
            other_track = cfg.dsc
        else:
            ref_ds = dsc_ds
            other_ds = asc_ds
            ref_track = cfg.dsc
            other_track = cfg.asc

        other_vrt = stack.enter_context(
            WarpedVRT(
                other_ds,
                crs=ref_ds.crs,
                transform=ref_ds.transform,
                width=ref_ds.width,
                height=ref_ds.height,
                resampling=cfg.velocity_resampling,
            )
        )

        ref_coh_ds = (
            stack.enter_context(rasterio.open(ref_track.coherence_file))
            if ref_track.coherence_file
            else None
        )
        other_coh_ds = (
            stack.enter_context(rasterio.open(other_track.coherence_file))
            if other_track.coherence_file
            else None
        )
        other_coh_vrt = (
            stack.enter_context(
                WarpedVRT(
                    other_coh_ds,
                    crs=ref_ds.crs,
                    transform=ref_ds.transform,
                    width=ref_ds.width,
                    height=ref_ds.height,
                    resampling=cfg.coherence_resampling,
                )
            )
            if other_coh_ds is not None
            else None
        )

        if cfg.target_grid == "asc":
            asc_reader = ref_ds
            dsc_reader = other_vrt
            asc_coh_reader = ref_coh_ds
            dsc_coh_reader = other_coh_vrt
        else:
            asc_reader = other_vrt
            dsc_reader = ref_ds
            asc_coh_reader = other_coh_vrt
            dsc_coh_reader = ref_coh_ds

        out_float_profile = build_float_profile(ref_ds.profile)
        out_mask_profile = build_uint8_profile(ref_ds.profile)

        east_writer = stack.enter_context(rasterio.open(east_out, "w", **out_float_profile))
        up_writer = stack.enter_context(rasterio.open(up_out, "w", **out_float_profile))
        valid_writer = stack.enter_context(rasterio.open(valid_out, "w", **out_mask_profile))
        cond_writer = stack.enter_context(rasterio.open(cond_out, "w", **out_float_profile))
        consistency_writer = (
            stack.enter_context(rasterio.open(consistency_out, "w", **out_float_profile))
            if cfg.write_consistency_error
            else None
        )

        windows = list(ref_ds.block_windows(1))
        progress = tqdm(total=len(windows), desc="LOS decomposition", unit="block")
        try:
            for _, window in windows:
                asc = asc_reader.read(1, window=window, out_dtype="float32")
                dsc = dsc_reader.read(1, window=window, out_dtype="float32")

                valid = valid_data_mask(asc, asc_reader.nodata) & valid_data_mask(dsc, dsc_reader.nodata)
                before_nodata = int(np.count_nonzero(valid))

                asc_coh = None
                dsc_coh = None
                if asc_coh_reader is not None:
                    asc_coh = asc_coh_reader.read(1, window=window, out_dtype="float32")
                    valid &= valid_data_mask(asc_coh, asc_coh_reader.nodata)
                if dsc_coh_reader is not None:
                    dsc_coh = dsc_coh_reader.read(1, window=window, out_dtype="float32")
                    valid &= valid_data_mask(dsc_coh, dsc_coh_reader.nodata)

                after_nodata = int(np.count_nonzero(valid))
                nodata_screened += max(0, before_nodata - after_nodata)

                if cfg.min_temporal_coherence > 0.0:
                    before_coh = after_nodata
                    if asc_coh is not None:
                        valid &= asc_coh >= cfg.min_temporal_coherence
                    if dsc_coh is not None:
                        valid &= dsc_coh >= cfg.min_temporal_coherence
                    after_coh = int(np.count_nonzero(valid))
                    coherence_screened += max(0, before_coh - after_coh)

                east = (inv[0, 0] * asc + inv[0, 1] * dsc).astype(np.float32)
                up = (inv[1, 0] * asc + inv[1, 1] * dsc).astype(np.float32)

                east[~valid] = np.nan
                up[~valid] = np.nan

                east_writer.write(east, 1, window=window)
                up_writer.write(up, 1, window=window)
                valid_writer.write(valid.astype(np.uint8), 1, window=window)

                cond_arr = np.full(valid.shape, condition_number, dtype=np.float32)
                cond_arr[~valid] = np.nan
                cond_writer.write(cond_arr, 1, window=window)

                if consistency_writer is not None:
                    asc_hat = cfg.asc.los_east_coeff * east + cfg.asc.los_up_coeff * up
                    dsc_hat = cfg.dsc.los_east_coeff * east + cfg.dsc.los_up_coeff * up
                    err = np.sqrt(0.5 * ((asc - asc_hat) ** 2 + (dsc - dsc_hat) ** 2)).astype(np.float32)
                    err[~valid] = np.nan
                    consistency_writer.write(err, 1, window=window)

                total_pixels += valid.size
                valid_pixels += int(np.count_nonzero(valid))
                progress.update(1)
        finally:
            progress.close()

    raster_viz_summary = run_raster_viz_exports(
        cfg=cfg.raster_viz,
        east_file=east_out,
        up_file=up_out,
        config_path=config_path,
    )
    point_export_summary = run_point_exports(
        cfg=cfg.point_exports,
        east_file=east_out,
        up_file=up_out,
        config_path=config_path,
    )

    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "target_grid": cfg.target_grid,
        "asc": {
            "name": cfg.asc.name,
            "velocity_file": str(cfg.asc.velocity_file),
            "coherence_file": str(cfg.asc.coherence_file) if cfg.asc.coherence_file else None,
            "los_east_coeff": cfg.asc.los_east_coeff,
            "los_up_coeff": cfg.asc.los_up_coeff,
            "los_coeff_source": cfg.asc.los_coeff_source,
            "los_coeff_details": cfg.asc.los_coeff_details,
        },
        "dsc": {
            "name": cfg.dsc.name,
            "velocity_file": str(cfg.dsc.velocity_file),
            "coherence_file": str(cfg.dsc.coherence_file) if cfg.dsc.coherence_file else None,
            "los_east_coeff": cfg.dsc.los_east_coeff,
            "los_up_coeff": cfg.dsc.los_up_coeff,
            "los_coeff_source": cfg.dsc.los_coeff_source,
            "los_coeff_details": cfg.dsc.los_coeff_details,
        },
        "matrix": {
            "determinant": determinant,
            "condition_number": condition_number,
            "condition_threshold": cfg.max_condition_number,
        },
        "filters": {
            "min_temporal_coherence": cfg.min_temporal_coherence,
        },
        "stats": {
            "total_pixels": int(total_pixels),
            "valid_pixels": int(valid_pixels),
            "valid_fraction": float(valid_pixels / total_pixels) if total_pixels > 0 else 0.0,
            "nodata_screened_pixels": int(nodata_screened),
            "coherence_screened_pixels": int(coherence_screened),
        },
        "outputs": {
            "east_velocity_m_per_year": str(east_out),
            "up_velocity_m_per_year": str(up_out),
            "valid_mask": str(valid_out),
            "condition_number": str(cond_out),
            "consistency_error_m_per_year": str(consistency_out) if cfg.write_consistency_error else None,
        },
        "exports": {
            "raster_viz": raster_viz_summary,
            "point_exports": point_export_summary,
        },
    }

    write_json_atomic(summary_out, summary)

    print("Decomposition completed.")
    print(f"East velocity: {east_out}")
    print(f"Up velocity: {up_out}")
    print(f"Summary: {summary_out}")
    return 0


def print_track_coeff_details(label: str, track: TrackConfig) -> None:
    """Emit concise terminal diagnostics for auto-derived coefficients."""
    if not track.los_coeff_details:
        return
    details = track.los_coeff_details
    incidence = details.get("incidence_median_deg")
    azimuth = details.get("los_azimuth_median_deg")
    sampled = details.get("file_pair_count_sampled")
    total = details.get("file_pair_count_total")
    if incidence is None or azimuth is None:
        print(f"{label} auto-coeff diagnostics: unavailable.")
        return
    print(
        f"{label} auto-coeff diagnostics: "
        f"incidence_med={incidence:.3f} deg, "
        f"los_azimuth_med={azimuth:.3f} deg, "
        f"sampled_pairs={sampled}/{total}"
    )


def main() -> int:
    """Parse args and run LOS decomposition."""
    parser = argparse.ArgumentParser(
        description=(
            "Decompose ascending/descending LOS velocity into East/Up components "
            "from [processing.decomposition] in processing_configuration.toml."
        )
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to processing/decomposition TOML config.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config/geometry and print planned outputs only.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    config_path = resolve_path(repo_root, args.config)
    cfg = parse_config(repo_root=repo_root, config_path=config_path)
    return run_decomposition(cfg=cfg, config_path=config_path, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
