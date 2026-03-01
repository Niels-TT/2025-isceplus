#!/usr/bin/env python3
"""Prepare Dolphin displacement workflow config from COMPASS CSLC outputs.

Technical summary:
    Discovers valid COMPASS CSLC HDF5 files, writes a reproducible CSLC file
    list, and generates a Dolphin YAML config using the project AOI/settings.

Why:
    Keep the post-coreg time-series stage deterministic and fail fast when the
    coregistered stack is incomplete.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import h5py

from stack_common import (
    DEFAULT_STACK_CONFIG_REL,
    buffer_bbox,
    infer_stack_root,
    kml_bbox,
    read_toml,
    resolve_path,
    resolve_stack_config,
)


OPTION_ALIASES = {
    "subdataset": "input-options.subdataset",
    "sds": "input-options.subdataset",
    "ms": "phase-linking.ministack-size",
    "hwy": "phase-linking.half-window.y",
    "hwx": "phase-linking.half-window.x",
    "n-parallel-bursts": "worker-settings.n-parallel-bursts",
    "use-evd": "phase-linking.use-evd",
    "slc-files": "cslc",
    "cslc-file-list": "cslc",
    "sx": "output-options.strides.x",
    "sy": "output-options.strides.y",
}


def command_exists(cmd: str) -> bool:
    """Check whether a command is available on PATH.

    Args:
        cmd: Command name.

    Returns:
        True when command is found, otherwise False.
    """
    return shutil.which(cmd) is not None


def dolphin_supports_cslc_file_list() -> bool:
    """Check whether installed Dolphin supports `--cslc-file-list`."""
    try:
        proc = subprocess.run(
            ["dolphin", "config", "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001
        return False
    return "--cslc-file-list" in proc.stdout


def discover_cslc_candidates(
    compass_work_dir: Path,
    cslc_glob: str,
    allow_recursive_fallback: bool,
) -> tuple[list[Path], str]:
    """Find COMPASS CSLC HDF5 candidate files.

    Args:
        compass_work_dir: COMPASS work directory.
        cslc_glob: Primary glob pattern relative to COMPASS work dir.
        allow_recursive_fallback: Whether to allow `**/*.h5` fallback search.

    Returns:
        Tuple of (sorted candidate files, discovery mode label).
    """
    candidates = sorted(compass_work_dir.glob(cslc_glob))
    if candidates:
        return candidates, f"glob:{cslc_glob}"
    if not allow_recursive_fallback:
        return [], "glob:no_matches"
    return sorted(compass_work_dir.rglob("*.h5")), "recursive_fallback:**/*.h5"


def validate_cslc(path: Path, subdataset: str) -> tuple[bool, str]:
    """Validate a CSLC HDF5 file against expected complex raster dataset.

    Args:
        path: CSLC HDF5 path.
        subdataset: HDF5 dataset path (for example `data/VV`).

    Returns:
        Tuple of (is_valid, reason).
    """
    key = subdataset.lstrip("/")
    try:
        with h5py.File(path, "r") as f:
            if key not in f:
                return False, f"missing dataset '{subdataset}'"
            ds = f[key]
            if ds.ndim != 2:
                return False, f"dataset '{subdataset}' is not 2D"
            if ds.shape[0] == 0 or ds.shape[1] == 0:
                return False, f"dataset '{subdataset}' has empty dimensions"
    except OSError as exc:
        return False, f"cannot open HDF5 ({exc})"
    return True, "ok"


def bool_cfg(cfg: dict, key: str, default: bool) -> bool:
    """Read a boolean-like config value with a default."""
    return bool(cfg.get(key, default))


def int_pair_cfg(value: object, key_name: str) -> tuple[int, int] | None:
    """Read and validate a positive integer pair from config.

    Args:
        value: Config value expected to be a 2-item list/tuple.
        key_name: Fully qualified config key name for error context.

    Returns:
        `(rows, cols)` when provided, otherwise `None`.

    Raises:
        ValueError: If shape is not a 2-item positive integer pair.
    """
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{key_name} must be a 2-item list like [rows, cols].")
    try:
        rows = int(value[0])
        cols = int(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key_name} must contain integers.") from exc
    if rows <= 0 or cols <= 0:
        raise ValueError(f"{key_name} must contain positive integers.")
    return rows, cols


def add_opt(cmd: list[str], flag: str, value: object) -> None:
    """Append a CLI flag and value when value is not empty.

    Args:
        cmd: Mutable command list.
        flag: CLI flag.
        value: Value to append.
    """
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    cmd += [flag, str(value)]


def add_bool_opt(cmd: list[str], true_flag: str, false_flag: str, enabled: bool) -> None:
    """Append one of two boolean CLI flags."""
    cmd.append(true_flag if enabled else false_flag)


def list_of_str(value: object) -> list[str]:
    """Validate and normalize a list-like config value to list[str].

    Args:
        value: Candidate list value from config.

    Returns:
        Normalized list of strings.

    Raises:
        ValueError: If value is not a list.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Expected a list value.")
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def dict_of_overrides(value: object) -> dict[str, object]:
    """Validate a table of Dolphin CLI option overrides.

    Args:
        value: Candidate mapping from config.

    Returns:
        Normalized map of option-key to value.

    Raises:
        ValueError: If the value is not a flat dictionary.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Expected a table/dict value.")
    out: dict[str, object] = {}
    for raw_key, raw_val in value.items():
        key = str(raw_key).strip()
        if not key:
            raise ValueError("Empty option key in option_overrides.")
        if isinstance(raw_val, dict):
            raise ValueError(
                f"Nested table for option '{key}' is not supported. "
                "Use a scalar/list/bool value."
            )
        out[key] = raw_val
    return out


def add_cli_override(cmd: list[str], option_key: str, option_value: object) -> None:
    """Append an arbitrary Dolphin CLI override option.

    Args:
        cmd: Mutable command list.
        option_key: Dolphin option key (with or without leading `--`).
        option_value: Value from TOML (`bool`, scalar, list, or `None`).

    Raises:
        ValueError: If an unsupported value type is provided.
    """
    if option_value is None:
        return
    if isinstance(option_value, str) and not option_value.strip():
        return

    key = option_key.strip().lstrip("-")
    if not key:
        raise ValueError("Override option key is empty.")

    if isinstance(option_value, bool):
        segments = key.split(".")
        last = segments[-1]
        prefix = ".".join(segments[:-1])

        def compose(last_seg: str) -> str:
            return f"{prefix}.{last_seg}" if prefix else last_seg

        if last.startswith("no-"):
            positive = compose(last[3:])
            negative = compose(last)
            cmd.append(f"--{negative}" if option_value else f"--{positive}")
        else:
            positive = compose(last)
            negative = compose(f"no-{last}")
            cmd.append(f"--{positive}" if option_value else f"--{negative}")
        return

    if isinstance(option_value, list):
        if not option_value:
            return
        cmd.append(f"--{key}")
        for item in option_value:
            if isinstance(item, (list, tuple)):
                cmd.extend(str(subitem) for subitem in item)
            else:
                cmd.append(str(item))
        return

    if isinstance(option_value, str):
        cmd += [f"--{key}", option_value]
        return

    if isinstance(option_value, (int, float)):
        cmd += [f"--{key}", str(option_value)]
        return

    raise ValueError(
        f"Unsupported value type for option '{option_key}': "
        f"{type(option_value).__name__}"
    )


def build_inline_cslc_fallback_cmd(cmd: list[str], cslc_files: list[Path]) -> list[str] | None:
    """Build Dolphin config command fallback using inline `--cslc` entries.

    Why:
        Some Dolphin versions do not parse `--cslc-file-list` consistently.
        This fallback keeps compatibility without changing user config.

    Args:
        cmd: Original command list containing `--cslc-file-list <path>`.
        cslc_files: Valid CSLC file paths to inline.

    Returns:
        Fallback command list, or None when no conversion is possible.
    """
    if "--cslc-file-list" not in cmd:
        return None
    idx = cmd.index("--cslc-file-list")
    if idx + 1 >= len(cmd):
        return None
    return [
        *cmd[:idx],
        "--cslc",
        *(str(p) for p in cslc_files),
        *cmd[idx + 2 :],
    ]


def canonical_option_key(option_name: str) -> str:
    """Canonicalize a Dolphin CLI option name for overlap checks.

    Args:
        option_name: Option key with or without leading `--`.

    Returns:
        Canonical option key without leading dashes and `no-` prefix.
    """
    key = option_name.strip().lstrip("-")
    if not key:
        return key
    segments = [seg[3:] if seg.startswith("no-") else seg for seg in key.split(".")]
    normalized = ".".join(segments)
    return OPTION_ALIASES.get(normalized, normalized)


def extract_option_keys(cli_args: list[str]) -> set[str]:
    """Extract canonical option keys from raw CLI token list.

    Args:
        cli_args: Raw command-line tokens.

    Returns:
        Set of canonical option keys present in token list.
    """
    keys: set[str] = set()
    for token in cli_args:
        if token.startswith("--") and len(token) > 2:
            keys.add(canonical_option_key(token))
    return keys


def mapped_option_keys() -> set[str]:
    """Return canonical Dolphin option keys managed by this wrapper."""
    return {
        "work-directory",
        "outfile",
        "input-options.subdataset",
        "output-options.bounds",
        "output-options.bounds-epsg",
        "phase-linking.ministack-size",
        "interferogram-network.max-bandwidth",
        "worker-settings.threads-per-worker",
        "worker-settings.n-parallel-bursts",
        "worker-settings.block-shape",
        "unwrap-options.n-parallel-jobs",
        "keep-paths-relative",
        "worker-settings.gpu-enabled",
        "unwrap-options.run-unwrap",
        "unwrap-options.run-goldstein",
        "unwrap-options.run-interpolation",
        "unwrap-options.zero-where-masked",
        "output-options.add-overviews",
        "phase-linking.use-evd",
        "phase-linking.mask-input-ps",
        "timeseries-options.run-inversion",
        "timeseries-options.run-velocity",
        "mask-file",
        "output-options.epsg",
        "phase-linking.half-window.x",
        "phase-linking.half-window.y",
        "phase-linking.shp-method",
        "phase-linking.shp-alpha",
        "phase-linking.beta",
        "phase-linking.baseline-lag",
        "interferogram-network.max-temporal-baseline",
        "unwrap-options.unwrap-method",
        "unwrap-options.snaphu-options.cost",
        "unwrap-options.snaphu-options.init-method",
        "unwrap-options.snaphu-options.ntiles",
        "unwrap-options.snaphu-options.tile-overlap",
        "unwrap-options.snaphu-options.n-parallel-tiles",
        "output-options.strides.x",
        "output-options.strides.y",
        "timeseries-options.method",
        "timeseries-options.correlation-threshold",
        "timeseries-options.block-shape",
        "timeseries-options.num-parallel-blocks",
        "cslc",
    }


def main() -> int:
    """Parse CLI args, validate CSLC inputs, and create Dolphin config.

    Returns:
        Exit code (0 for success).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Prepare Dolphin config and CSLC input list from COMPASS outputs. "
            "Fails by default if expected stack size is not reached."
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
        "--allow-partial-cslc",
        action="store_true",
        help="Allow generating Dolphin config with fewer CSLC files than expected.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print Dolphin command without writing files.",
    )
    parser.add_argument(
        "--skip-qc",
        action="store_true",
        help="Skip interferogram-network QC plot stage even if enabled in config.",
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

    search_cfg = cfg["search"]
    compass_cfg = cfg.get("processing", {}).get("compass", {})
    dolphin_cfg = cfg.get("processing", {}).get("dolphin", {})

    compass_work_dir = resolve_path(
        repo_root,
        compass_cfg.get("work_dir", str(stack_root / "stack" / "compass")),
    )
    dolphin_work_dir = resolve_path(
        repo_root,
        dolphin_cfg.get("work_dir", str(stack_root / "stack" / "dolphin")),
    )
    dolphin_config_file = resolve_path(
        repo_root,
        dolphin_cfg.get(
            "config_file",
            str(stack_root / "stack" / "dolphin" / "config" / "dolphin_config.yaml"),
        ),
    )
    cslc_file_list = resolve_path(
        repo_root,
        dolphin_cfg.get(
            "cslc_file_list",
            str(stack_root / "stack" / "dolphin" / "inputs" / "cslc_files.txt"),
        ),
    )
    cslc_glob = str(dolphin_cfg.get("cslc_glob", "t*/20*/t*.h5")).strip() or "t*/20*/t*.h5"
    allow_recursive_cslc_search = bool_cfg(dolphin_cfg, "allow_recursive_cslc_search", False)
    cslc_subdataset = str(dolphin_cfg.get("cslc_subdataset", "data/VV")).strip()
    bbox_buffer_deg = float(dolphin_cfg.get("bbox_buffer_deg", 0.0))
    ministack_size = int(dolphin_cfg.get("ministack_size", 15))
    max_bandwidth = int(dolphin_cfg.get("max_bandwidth", 3))
    gpu_enabled = bool_cfg(dolphin_cfg, "gpu_enabled", False)
    threads_per_worker = int(dolphin_cfg.get("threads_per_worker", 1))
    n_parallel_bursts = int(dolphin_cfg.get("n_parallel_bursts", 1))
    n_parallel_unwrap_jobs = int(dolphin_cfg.get("n_parallel_unwrap_jobs", 1))
    run_unwrap = bool_cfg(dolphin_cfg, "run_unwrap", True)
    keep_paths_relative = bool_cfg(dolphin_cfg, "keep_paths_relative", False)
    run_goldstein = bool_cfg(dolphin_cfg, "run_goldstein", False)
    run_interpolation = bool_cfg(dolphin_cfg, "run_interpolation", False)
    zero_where_masked = bool_cfg(dolphin_cfg, "zero_where_masked", False)
    add_overviews = bool_cfg(dolphin_cfg, "add_overviews", True)
    use_evd = bool_cfg(dolphin_cfg, "phase_linking_use_evd", False)
    mask_input_ps = bool_cfg(dolphin_cfg, "phase_linking_mask_input_ps", False)
    run_inversion = bool_cfg(dolphin_cfg, "timeseries_run_inversion", True)
    run_velocity = bool_cfg(dolphin_cfg, "timeseries_run_velocity", True)
    qc_cfg = dolphin_cfg.get("qc", {})
    qc_enabled = bool_cfg(qc_cfg, "enabled", False)

    # Optional scalar knobs (only forwarded when explicitly configured).
    mask_file = dolphin_cfg.get("mask_file")
    output_epsg = dolphin_cfg.get("output_epsg")
    phase_linking_half_window_x = dolphin_cfg.get("phase_linking_half_window_x")
    phase_linking_half_window_y = dolphin_cfg.get("phase_linking_half_window_y")
    phase_linking_shp_method = dolphin_cfg.get("phase_linking_shp_method")
    phase_linking_shp_alpha = dolphin_cfg.get("phase_linking_shp_alpha")
    phase_linking_beta = dolphin_cfg.get("phase_linking_beta")
    phase_linking_baseline_lag = dolphin_cfg.get("phase_linking_baseline_lag")
    max_temporal_baseline = dolphin_cfg.get("max_temporal_baseline")
    unwrap_method = dolphin_cfg.get("unwrap_method")
    snaphu_cost = dolphin_cfg.get("snaphu_cost")
    snaphu_init_method = dolphin_cfg.get("snaphu_init_method")
    snaphu_ntiles_x = dolphin_cfg.get("snaphu_ntiles_x")
    snaphu_ntiles_y = dolphin_cfg.get("snaphu_ntiles_y")
    snaphu_tile_overlap_x = dolphin_cfg.get("snaphu_tile_overlap_x")
    snaphu_tile_overlap_y = dolphin_cfg.get("snaphu_tile_overlap_y")
    snaphu_n_parallel_tiles = dolphin_cfg.get("snaphu_n_parallel_tiles")
    strides_x = dolphin_cfg.get("strides_x")
    strides_y = dolphin_cfg.get("strides_y")
    try:
        worker_block_shape = int_pair_cfg(
            dolphin_cfg.get("worker_block_shape"),
            "processing.dolphin.worker_block_shape",
        )
        timeseries_block_shape = int_pair_cfg(
            dolphin_cfg.get("timeseries_block_shape"),
            "processing.dolphin.timeseries_block_shape",
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    timeseries_method = dolphin_cfg.get("timeseries_method")
    timeseries_correlation_threshold = dolphin_cfg.get("timeseries_correlation_threshold")
    timeseries_num_parallel_blocks = dolphin_cfg.get("timeseries_num_parallel_blocks")
    reference_template_file_value = dolphin_cfg.get("reference_template_file", "")
    try:
        extra_cli_args = list_of_str(dolphin_cfg.get("extra_cli_args", []))
    except ValueError as exc:
        print(f"Invalid processing.dolphin.extra_cli_args: {exc}", file=sys.stderr)
        return 2
    allow_raw_extra_cli_args = bool_cfg(dolphin_cfg, "allow_raw_extra_cli_args", False)
    try:
        option_overrides = dict_of_overrides(dolphin_cfg.get("option_overrides", {}))
    except ValueError as exc:
        print(f"Invalid processing.dolphin.option_overrides: {exc}", file=sys.stderr)
        return 2
    if extra_cli_args and not allow_raw_extra_cli_args:
        print(
            "processing.dolphin.extra_cli_args is set, but raw passthrough is disabled by default.",
            file=sys.stderr,
        )
        print(
            "Use processing.dolphin.option_overrides for structured options, or set "
            "processing.dolphin.allow_raw_extra_cli_args=true to permit raw args.",
            file=sys.stderr,
        )
        return 2

    if not command_exists("dolphin"):
        print("Missing command: dolphin. Install/update it in isce3-feb.", file=sys.stderr)
        return 2
    if not compass_work_dir.exists():
        print(f"Missing COMPASS work dir: {compass_work_dir}", file=sys.stderr)
        print("Run COMPASS coregistration first.", file=sys.stderr)
        return 2
    if not cslc_subdataset:
        print("processing.dolphin.cslc_subdataset is empty in processing_configuration.toml.", file=sys.stderr)
        return 2

    candidates, discovery_mode = discover_cslc_candidates(
        compass_work_dir=compass_work_dir,
        cslc_glob=cslc_glob,
        allow_recursive_fallback=allow_recursive_cslc_search,
    )
    if not candidates:
        print(
            "No CSLC candidates found under COMPASS work dir. "
            f"Primary glob '{cslc_glob}' returned no matches.",
            file=sys.stderr,
        )
        if not allow_recursive_cslc_search:
            print(
                "Set processing.dolphin.allow_recursive_cslc_search=true only if you need "
                "a broad fallback search.",
                file=sys.stderr,
            )
        return 3
    valid_cslc: list[Path] = []
    invalid_cslc: list[tuple[Path, str]] = []
    for path in candidates:
        ok, reason = validate_cslc(path, cslc_subdataset)
        if ok:
            valid_cslc.append(path)
        else:
            invalid_cslc.append((path, reason))

    expected_unique_dates = int(search_cfg.get("expected_unique_dates", 0))
    if (
        expected_unique_dates > 0
        and len(valid_cslc) < expected_unique_dates
        and not args.allow_partial_cslc
    ):
        print(
            "COMPASS stack appears incomplete for Dolphin stage: "
            f"valid CSLC files={len(valid_cslc)} "
            f"< expected_unique_dates={expected_unique_dates}.",
            file=sys.stderr,
        )
        print("Finish coregistration or pass --allow-partial-cslc.", file=sys.stderr)
        if invalid_cslc:
            print("First invalid CSLC entries:", file=sys.stderr)
            for path, reason in invalid_cslc[:10]:
                print(f"  - {path}: {reason}", file=sys.stderr)
        return 3
    if len(valid_cslc) < 2:
        print(
            f"Need at least 2 valid CSLC files for Dolphin, found {len(valid_cslc)}.",
            file=sys.stderr,
        )
        return 3

    kml_path = resolve_path(repo_root, cfg["aoi"]["kml"])
    west, south, east, north = buffer_bbox(kml_bbox(kml_path), bbox_buffer_deg)

    cmd = [
        "dolphin",
        "config",
        "--work-directory",
        str(dolphin_work_dir),
        "--outfile",
        str(dolphin_config_file),
        "--input-options.subdataset",
        cslc_subdataset,
        "--output-options.bounds",
        f"{west:.8f}",
        f"{south:.8f}",
        f"{east:.8f}",
        f"{north:.8f}",
        "--output-options.bounds-epsg",
        "4326",
        "--phase-linking.ministack-size",
        str(ministack_size),
        "--interferogram-network.max-bandwidth",
        str(max_bandwidth),
        "--worker-settings.threads-per-worker",
        str(threads_per_worker),
        "--worker-settings.n-parallel-bursts",
        str(n_parallel_bursts),
        "--unwrap-options.n-parallel-jobs",
        str(n_parallel_unwrap_jobs),
    ]
    if worker_block_shape is not None:
        cmd += [
            "--worker-settings.block-shape",
            str(worker_block_shape[0]),
            str(worker_block_shape[1]),
        ]
    add_bool_opt(cmd, "--keep-paths-relative", "--no-keep-paths-relative", keep_paths_relative)
    add_bool_opt(cmd, "--worker-settings.gpu-enabled", "--worker-settings.no-gpu-enabled", gpu_enabled)
    add_bool_opt(cmd, "--unwrap-options.run-unwrap", "--unwrap-options.no-run-unwrap", run_unwrap)
    add_bool_opt(cmd, "--unwrap-options.run-goldstein", "--unwrap-options.no-run-goldstein", run_goldstein)
    add_bool_opt(
        cmd,
        "--unwrap-options.run-interpolation",
        "--unwrap-options.no-run-interpolation",
        run_interpolation,
    )
    add_bool_opt(
        cmd,
        "--unwrap-options.zero-where-masked",
        "--unwrap-options.no-zero-where-masked",
        zero_where_masked,
    )
    add_bool_opt(
        cmd,
        "--output-options.add-overviews",
        "--output-options.no-add-overviews",
        add_overviews,
    )
    add_bool_opt(cmd, "--phase-linking.use-evd", "--phase-linking.no-use-evd", use_evd)
    add_bool_opt(
        cmd,
        "--phase-linking.mask-input-ps",
        "--phase-linking.no-mask-input-ps",
        mask_input_ps,
    )
    add_bool_opt(
        cmd,
        "--timeseries-options.run-inversion",
        "--timeseries-options.no-run-inversion",
        run_inversion,
    )
    add_bool_opt(
        cmd,
        "--timeseries-options.run-velocity",
        "--timeseries-options.no-run-velocity",
        run_velocity,
    )

    add_opt(cmd, "--mask-file", mask_file)
    add_opt(cmd, "--output-options.epsg", output_epsg)
    add_opt(cmd, "--phase-linking.half-window.x", phase_linking_half_window_x)
    add_opt(cmd, "--phase-linking.half-window.y", phase_linking_half_window_y)
    add_opt(cmd, "--phase-linking.shp-method", phase_linking_shp_method)
    add_opt(cmd, "--phase-linking.shp-alpha", phase_linking_shp_alpha)
    add_opt(cmd, "--phase-linking.beta", phase_linking_beta)
    add_opt(cmd, "--phase-linking.baseline-lag", phase_linking_baseline_lag)
    add_opt(cmd, "--interferogram-network.max-temporal-baseline", max_temporal_baseline)
    add_opt(cmd, "--unwrap-options.unwrap-method", unwrap_method)
    add_opt(cmd, "--unwrap-options.snaphu-options.cost", snaphu_cost)
    add_opt(cmd, "--unwrap-options.snaphu-options.init-method", snaphu_init_method)
    if snaphu_ntiles_x is not None and snaphu_ntiles_y is not None:
        cmd += [
            "--unwrap-options.snaphu-options.ntiles",
            str(snaphu_ntiles_y),
            str(snaphu_ntiles_x),
        ]
    if snaphu_tile_overlap_x is not None and snaphu_tile_overlap_y is not None:
        cmd += [
            "--unwrap-options.snaphu-options.tile-overlap",
            str(snaphu_tile_overlap_y),
            str(snaphu_tile_overlap_x),
        ]
    add_opt(cmd, "--unwrap-options.snaphu-options.n-parallel-tiles", snaphu_n_parallel_tiles)
    add_opt(cmd, "--sx", strides_x)
    add_opt(cmd, "--sy", strides_y)
    add_opt(cmd, "--timeseries-options.method", timeseries_method)
    add_opt(cmd, "--timeseries-options.correlation-threshold", timeseries_correlation_threshold)
    if timeseries_block_shape is not None:
        cmd += [
            "--timeseries-options.block-shape",
            str(timeseries_block_shape[0]),
            str(timeseries_block_shape[1]),
        ]
    add_opt(cmd, "--timeseries-options.num-parallel-blocks", timeseries_num_parallel_blocks)

    mapped_keys = mapped_option_keys()
    override_keys = {canonical_option_key(k) for k in option_overrides}
    extra_keys = extract_option_keys(extra_cli_args)
    overlap_mapped_overrides = sorted(mapped_keys & override_keys)
    overlap_mapped_extra = sorted(mapped_keys & extra_keys)
    overlap_overrides_extra = sorted(override_keys & extra_keys)
    if overlap_mapped_overrides:
        print(
            "Conflicting Dolphin options: processing.dolphin.option_overrides overlaps "
            f"wrapper-managed options: {', '.join(overlap_mapped_overrides)}",
            file=sys.stderr,
        )
        print("Use mapped TOML keys for these options and remove duplicates.", file=sys.stderr)
        return 2
    if overlap_mapped_extra:
        print(
            "Conflicting Dolphin options: processing.dolphin.extra_cli_args overlaps "
            f"wrapper-managed options: {', '.join(overlap_mapped_extra)}",
            file=sys.stderr,
        )
        print("Use mapped TOML keys for these options and remove duplicates.", file=sys.stderr)
        return 2
    if overlap_overrides_extra:
        print(
            "Conflicting Dolphin options: option_overrides overlaps extra_cli_args: "
            f"{', '.join(overlap_overrides_extra)}",
            file=sys.stderr,
        )
        print("Keep each option in only one place to avoid ambiguity.", file=sys.stderr)
        return 2

    for opt_key, opt_val in option_overrides.items():
        try:
            add_cli_override(cmd, opt_key, opt_val)
        except ValueError as exc:
            print(
                "Invalid processing.dolphin.option_overrides entry "
                f"'{opt_key}': {exc}",
                file=sys.stderr,
            )
            return 2

    supports_cslc_file_list = dolphin_supports_cslc_file_list()
    if supports_cslc_file_list:
        cmd += ["--cslc-file-list", str(cslc_file_list)]
    else:
        cmd += ["--cslc", *(str(p) for p in valid_cslc)]
    cmd += extra_cli_args
    fallback_cmd = (
        build_inline_cslc_fallback_cmd(cmd, valid_cslc)
        if supports_cslc_file_list
        else None
    )

    reference_template_file = (
        resolve_path(repo_root, reference_template_file_value)
        if isinstance(reference_template_file_value, str) and reference_template_file_value.strip()
        else None
    )
    reference_cmd = (
        [
            "dolphin",
            "config",
            "--print-empty",
            "--outfile",
            str(reference_template_file),
        ]
        if reference_template_file is not None
        else None
    )
    qc_script = Path(__file__).with_name("10_plot_ifg_network_qc.py")
    qc_cmd = [
        sys.executable,
        str(qc_script),
        "--repo-root",
        str(repo_root),
        "--config",
        str(config_path),
    ]

    print(f"Config: {config_path}")
    print(f"COMPASS work dir: {compass_work_dir}")
    print(f"Dolphin work dir: {dolphin_work_dir}")
    print(f"Dolphin config output: {dolphin_config_file}")
    print(f"CSLC list file: {cslc_file_list}")
    print(f"CSLC discovery glob: {cslc_glob}")
    print(f"CSLC discovery mode: {discovery_mode}")
    print(f"CSLC recursive fallback allowed: {allow_recursive_cslc_search}")
    print(f"CSLC subdataset: {cslc_subdataset}")
    print(f"CSLC candidates found: {len(candidates)}")
    print(f"CSLC valid for Dolphin: {len(valid_cslc)}")
    print(f"CSLC invalid/skipped: {len(invalid_cslc)}")
    print(f"Expected unique dates: {expected_unique_dates}")
    print(f"Worker block shape: {worker_block_shape}")
    print(f"Timeseries block shape: {timeseries_block_shape}")
    print(f"BBox (W,S,E,N): {west:.6f}, {south:.6f}, {east:.6f}, {north:.6f}")
    print(f"Dolphin option_overrides from TOML: {len(option_overrides)}")
    print(f"Extra Dolphin CLI args from TOML: {len(extra_cli_args)}")
    print(f"Raw extra_cli_args allowed: {allow_raw_extra_cli_args}")
    print(f"Dolphin supports --cslc-file-list: {supports_cslc_file_list}")
    print(f"QC network plot enabled: {qc_enabled and not args.skip_qc}")
    print("\nDolphin config command:")
    print(" ".join(cmd))
    if fallback_cmd is not None:
        print("\nDolphin fallback command (if --cslc-file-list is rejected):")
        print(" ".join(fallback_cmd))
    if reference_cmd is not None:
        print("\nDolphin reference-template command:")
        print(" ".join(reference_cmd))
    if qc_enabled and not args.skip_qc:
        print("\nDolphin QC command:")
        print(" ".join(qc_cmd))

    if args.dry_run:
        return 0

    cslc_file_list.parent.mkdir(parents=True, exist_ok=True)
    dolphin_config_file.parent.mkdir(parents=True, exist_ok=True)
    dolphin_work_dir.mkdir(parents=True, exist_ok=True)
    if reference_template_file is not None:
        reference_template_file.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(reference_cmd, check=True)
    cslc_file_list.write_text(
        "".join(f"{p}\n" for p in valid_cslc),
        encoding="utf-8",
    )

    used_inline_cslc_fallback = False
    effective_cmd = cmd
    if fallback_cmd is None:
        subprocess.run(cmd, check=True)
    else:
        primary = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        if primary.returncode == 0:
            if primary.stdout.strip():
                print(primary.stdout.strip())
            if primary.stderr.strip():
                print(primary.stderr.strip())
        else:
            print(
                "[WARN] Dolphin config failed with --cslc-file-list; "
                "retrying with inline --cslc entries for compatibility."
            )
            if primary.stderr.strip():
                stderr_lines = [ln for ln in primary.stderr.strip().splitlines() if ln.strip()]
                if stderr_lines:
                    print(f"[WARN] Dolphin error tail: {stderr_lines[-1]}")
            subprocess.run(fallback_cmd, check=True)
            used_inline_cslc_fallback = True
            effective_cmd = fallback_cmd

    qc_executed = False
    if qc_enabled and not args.skip_qc:
        if not qc_script.exists():
            print(f"Missing QC script: {qc_script}", file=sys.stderr)
            return 2
        subprocess.run(qc_cmd, check=True)
        qc_executed = True

    summary = {
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "compass_work_dir": str(compass_work_dir),
        "dolphin_work_dir": str(dolphin_work_dir),
        "dolphin_config_file": str(dolphin_config_file),
        "cslc_file_list": str(cslc_file_list),
        "cslc_glob": cslc_glob,
        "cslc_discovery_mode": discovery_mode,
        "allow_recursive_cslc_search": allow_recursive_cslc_search,
        "cslc_subdataset": cslc_subdataset,
        "cslc_candidates_count": len(candidates),
        "cslc_valid_count": len(valid_cslc),
        "cslc_invalid_count": len(invalid_cslc),
        "expected_unique_dates": expected_unique_dates,
        "worker_block_shape": worker_block_shape,
        "timeseries_block_shape": timeseries_block_shape,
        "bbox_wsen": [west, south, east, north],
        "option_overrides": option_overrides,
        "extra_cli_args": extra_cli_args,
        "allow_raw_extra_cli_args": allow_raw_extra_cli_args,
        "reference_template_file": str(reference_template_file) if reference_template_file else None,
        "qc_enabled": qc_enabled,
        "qc_skipped_cli": args.skip_qc,
        "qc_executed": qc_executed,
        "qc_command": qc_cmd if qc_enabled and not args.skip_qc else None,
        "used_inline_cslc_fallback": used_inline_cslc_fallback,
        "command": effective_cmd,
    }
    summary_path = dolphin_work_dir / "prepare_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\nDolphin config generated.")
    print(f"Summary: {summary_path}")
    print("Next: run scripts/11_run_dolphin_workflow.py to execute Dolphin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
