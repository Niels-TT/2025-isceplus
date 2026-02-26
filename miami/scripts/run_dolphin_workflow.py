#!/usr/bin/env python3
"""Run Dolphin displacement workflow from prepared YAML config.

Technical summary:
    Resolves Dolphin config path from stack TOML (or CLI override) and executes
    `dolphin run` with optional debug logging. Optionally runs config-driven
    point exports (CSV/KMZ) after successful completion.

Why:
    Keep execution reproducible and consistent with the project config wiring,
    including post-processing artifacts used in operational point workflows.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from stack_common import DEFAULT_STACK_CONFIG_REL, read_toml, resolve_path


def command_exists(cmd: str) -> bool:
    """Check whether a command is available on PATH."""
    return shutil.which(cmd) is not None


def should_run_point_export(cfg: dict) -> bool:
    """Check whether point export is enabled in stack config."""
    return bool(
        cfg.get("processing", {})
        .get("dolphin", {})
        .get("point_exports", {})
        .get("enabled", False)
    )


def should_run_raster_viz_export(cfg: dict) -> bool:
    """Check whether raster visualization export is enabled in stack config."""
    return bool(
        cfg.get("processing", {})
        .get("dolphin", {})
        .get("raster_viz", {})
        .get("enabled", False)
    )


def run_point_export(repo_root: Path, stack_config: Path, dry_run: bool = False) -> None:
    """Execute point export helper script.

    Args:
        repo_root: Repository root directory.
        stack_config: Absolute stack config path.
        dry_run: Whether to pass `--dry-run`.
    """
    exporter = Path(__file__).with_name("export_dolphin_points.py")
    if not exporter.exists():
        print(f"Point export script missing: {exporter}", file=sys.stderr)
        raise FileNotFoundError(exporter)

    cmd = [
        sys.executable,
        str(exporter),
        "--repo-root",
        str(repo_root),
        "--config",
        str(stack_config),
    ]
    if dry_run:
        cmd.append("--dry-run")

    print(f"Point export command: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def run_raster_viz_export(repo_root: Path, stack_config: Path, dry_run: bool = False) -> None:
    """Execute raster visualization export helper script.

    Args:
        repo_root: Repository root directory.
        stack_config: Absolute stack config path.
        dry_run: Whether to pass `--dry-run`.
    """
    exporter = Path(__file__).with_name("export_dolphin_raster_viz.py")
    if not exporter.exists():
        print(f"Raster viz export script missing: {exporter}", file=sys.stderr)
        raise FileNotFoundError(exporter)

    cmd = [
        sys.executable,
        str(exporter),
        "--repo-root",
        str(repo_root),
        "--config",
        str(stack_config),
    ]
    if dry_run:
        cmd.append("--dry-run")

    print(f"Raster viz export command: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main() -> int:
    """Parse CLI args and execute Dolphin workflow.

    Returns:
        Exit code (0 for success).
    """
    parser = argparse.ArgumentParser(
        description="Execute Dolphin run using the prepared Dolphin config."
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
        "--dolphin-config",
        default="",
        help="Optional override for Dolphin YAML config path.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Dolphin debug mode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved command without executing.",
    )
    parser.add_argument(
        "--skip-point-export",
        action="store_true",
        help="Skip CSV/KMZ point export stage after Dolphin run.",
    )
    parser.add_argument(
        "--skip-raster-viz-export",
        action="store_true",
        help="Skip raster quicklook export stage after Dolphin run.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    stack_config = resolve_path(repo_root, args.config)
    cfg = read_toml(stack_config)
    dolphin_cfg = cfg.get("processing", {}).get("dolphin", {})

    dolphin_config_value = args.dolphin_config or dolphin_cfg.get(
        "config_file",
        "miami/insar/us_isleofnormandy_s1_asc_t48/stack/dolphin/config/dolphin_config.yaml",
    )
    dolphin_config = resolve_path(repo_root, dolphin_config_value)

    if not command_exists("dolphin"):
        print("Missing command: dolphin. Install/update it in isce3-feb.", file=sys.stderr)
        return 2
    if not dolphin_config.exists():
        print(f"Missing Dolphin config: {dolphin_config}", file=sys.stderr)
        print("Run miami/scripts/prepare_dolphin_workflow.py first.", file=sys.stderr)
        return 2

    cmd = ["dolphin", "run", str(dolphin_config)]
    if args.debug:
        cmd.append("--debug")

    print(f"Stack config: {stack_config}")
    print(f"Dolphin config: {dolphin_config}")
    print(f"Dolphin command: {' '.join(cmd)}")
    if args.dry_run:
        if should_run_point_export(cfg) and not args.skip_point_export:
            run_point_export(repo_root=repo_root, stack_config=stack_config, dry_run=True)
        if should_run_raster_viz_export(cfg) and not args.skip_raster_viz_export:
            run_raster_viz_export(repo_root=repo_root, stack_config=stack_config, dry_run=True)
        return 0

    subprocess.run(cmd, check=True)
    if should_run_point_export(cfg) and not args.skip_point_export:
        run_point_export(repo_root=repo_root, stack_config=stack_config, dry_run=False)
    if should_run_raster_viz_export(cfg) and not args.skip_raster_viz_export:
        run_raster_viz_export(repo_root=repo_root, stack_config=stack_config, dry_run=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
