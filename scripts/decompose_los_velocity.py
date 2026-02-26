#!/usr/bin/env python3
"""Decompose ascending/descending LOS velocity rasters into East/Up components.

Technical summary:
    Loads two LOS velocity products (ascending + descending), aligns them to a
    common grid, solves a 2x2 system per pixel using user-provided LOS
    projection coefficients, and writes East/Up rasters plus QC artifacts.

Why:
    Adds an explicit decomposition stage on top of the existing
    COMPASS -> Dolphin pipeline while keeping raster outputs as the primary
    scientific products.
"""

from __future__ import annotations

import argparse
import contextlib
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

    if "los_east_coeff" not in section or "los_up_coeff" not in section:
        raise ValueError(
            f"processing.decomposition.track_{section_name} requires "
            "los_east_coeff and los_up_coeff."
        )
    los_east_coeff = float(section["los_east_coeff"])
    los_up_coeff = float(section["los_up_coeff"])

    return TrackConfig(
        name=name,
        velocity_file=velocity_file,
        coherence_file=coherence_file,
        los_east_coeff=los_east_coeff,
        los_up_coeff=los_up_coeff,
    )


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
        },
        "dsc": {
            "name": cfg.dsc.name,
            "velocity_file": str(cfg.dsc.velocity_file),
            "coherence_file": str(cfg.dsc.coherence_file) if cfg.dsc.coherence_file else None,
            "los_east_coeff": cfg.dsc.los_east_coeff,
            "los_up_coeff": cfg.dsc.los_up_coeff,
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
    }

    tmp = summary_out.with_suffix(summary_out.suffix + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    os.replace(tmp, summary_out)

    print("Decomposition completed.")
    print(f"East velocity: {east_out}")
    print(f"Up velocity: {up_out}")
    print(f"Summary: {summary_out}")
    return 0


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
