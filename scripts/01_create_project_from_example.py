#!/usr/bin/env python3
"""Create a new project workspace from the reusable example scaffold.

Why:
    Keep project setup consistent so all pipeline scripts can run with only a
    project-specific config path and AOI KML.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
import shutil
import sys
from pathlib import Path

TEXT_SUFFIXES = {
    ".md",
    ".toml",
    ".txt",
    ".kml",
    ".yaml",
    ".yml",
    ".json",
    ".sh",
    ".py",
}


@dataclass(frozen=True)
class StackSpec:
    """Definition for one generated stack."""

    name: str
    flight_direction: str


def validate_slug(value: str, field: str) -> str:
    """Validate a lowercase slug-like identifier.

    Args:
        value: Raw input string.
        field: Field name for error context.

    Returns:
        Normalized slug string.

    Raises:
        ValueError: If identifier contains unsupported characters.
    """
    text = value.strip().lower()
    if not text:
        raise ValueError(f"{field} cannot be empty.")
    if not re.fullmatch(r"[a-z0-9_\-]+", text):
        raise ValueError(
            f"{field} must match [a-z0-9_-]+ (got '{value}')."
        )
    return text


def replace_placeholders(root: Path, replacements: dict[str, str]) -> int:
    """Replace template placeholders in text files.

    Args:
        root: Root directory to update.
        replacements: Placeholder-to-value mapping.

    Returns:
        Number of files changed.
    """
    changed = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        updated = text
        for key, value in replacements.items():
            updated = updated.replace(key, value)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def set_quoted_value_in_section(
    lines: list[str],
    *,
    section_name: str,
    key: str,
    value: str,
) -> None:
    """Replace one quoted TOML key in a section while preserving inline comments."""
    current_section = ""
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip()
            continue
        if current_section != section_name:
            continue
        if not stripped.startswith(f"{key} ="):
            continue

        indent = line[: len(line) - len(line.lstrip())]
        comment = ""
        if "#" in line:
            comment = " " + line[line.index("#"):]
        lines[idx] = f'{indent}{key} = "{value}"{comment}'
        return

    raise ValueError(f"Missing key {key!r} in section [{section_name}]")


def update_stack_config(
    *,
    config_path: Path,
    flight_direction: str,
    asc_velocity_file: str,
    dsc_velocity_file: str,
    target_grid: str,
) -> None:
    """Patch per-stack config values after placeholder replacement."""
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    set_quoted_value_in_section(
        lines,
        section_name="search",
        key="flight_direction",
        value=flight_direction,
    )
    set_quoted_value_in_section(
        lines,
        section_name="processing.decomposition.track_asc",
        key="velocity_file",
        value=asc_velocity_file,
    )
    set_quoted_value_in_section(
        lines,
        section_name="processing.decomposition.track_dsc",
        key="velocity_file",
        value=dsc_velocity_file,
    )
    set_quoted_value_in_section(
        lines,
        section_name="processing.decomposition",
        key="target_grid",
        value=target_grid,
    )
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_project_readme(
    *,
    readme_path: Path,
    project_name: str,
    project_root_value: str,
    stacks: list[StackSpec],
) -> None:
    """Rewrite copied project README examples with actual project/stack names."""
    if not readme_path.exists():
        return

    primary_stack = stacks[0].name
    asc_stack = next((s.name for s in stacks if s.flight_direction == "ASCENDING"), primary_stack)
    dsc_stack = next((s.name for s in stacks if s.flight_direction == "DESCENDING"), primary_stack)

    text = readme_path.read_text(encoding="utf-8")
    updated = text
    replacements = {
        "# Example Project Template": f"# {project_name} Project Workflow",
        "projects/my_city": project_root_value,
        "my_city_s1_asc_t000": asc_stack,
        "my_city_s1_dsc_t000": dsc_stack,
        "my_city": project_name,
    }
    for old, new in replacements.items():
        updated = updated.replace(old, new)

    if updated != text:
        readme_path.write_text(updated, encoding="utf-8")


def main() -> int:
    """Parse CLI args and scaffold a new project from example_project."""
    parser = argparse.ArgumentParser(
        description="Create a new project from example_project template."
    )
    parser.add_argument(
        "--project-name",
        required=True,
        help="Project folder name under target root (slug: [a-z0-9_-]+).",
    )
    parser.add_argument(
        "--stack-name",
        default="",
        help=(
            "Stack identifier used under insar/<stack-name>. "
            "Default: <project-name>_s1_asc_t000"
        ),
    )
    parser.add_argument(
        "--flight-direction",
        default="ASCENDING",
        choices=["ASCENDING", "DESCENDING"],
        help="Single-stack mode only. Default: ASCENDING.",
    )
    parser.add_argument(
        "--dual-track",
        action="store_true",
        help="Create two stacks: one ASCENDING and one DESCENDING.",
    )
    parser.add_argument(
        "--asc-stack-name",
        default="",
        help=(
            "Ascending stack name used only with --dual-track. "
            "Default: <project-name>_s1_asc_t000"
        ),
    )
    parser.add_argument(
        "--dsc-stack-name",
        default="",
        help=(
            "Descending stack name used only with --dual-track. "
            "Default: <project-name>_s1_dsc_t000"
        ),
    )
    parser.add_argument(
        "--site-name",
        default="",
        help="Human-readable site label stored in config. Default: project name.",
    )
    parser.add_argument(
        "--target-root",
        default="projects",
        help="Relative/absolute root for generated projects (default: projects).",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing target project directory.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    template_root = repo_root / "example_project"
    if not template_root.exists():
        print(f"Missing template folder: {template_root}", file=sys.stderr)
        return 2

    try:
        project_name = validate_slug(args.project_name, "project-name")
        site_name = validate_slug(args.site_name or project_name, "site-name")
        if args.dual_track:
            if args.stack_name:
                raise ValueError("--stack-name cannot be used together with --dual-track.")
            if args.flight_direction != "ASCENDING":
                raise ValueError("--flight-direction is only valid in single-stack mode.")
            asc_default = f"{project_name}_s1_asc_t000"
            dsc_default = f"{project_name}_s1_dsc_t000"
            asc_stack_name = validate_slug(
                args.asc_stack_name or asc_default,
                "asc-stack-name",
            )
            dsc_stack_name = validate_slug(
                args.dsc_stack_name or dsc_default,
                "dsc-stack-name",
            )
            if asc_stack_name == dsc_stack_name:
                raise ValueError("asc-stack-name and dsc-stack-name must differ.")
            stacks = [
                StackSpec(name=asc_stack_name, flight_direction="ASCENDING"),
                StackSpec(name=dsc_stack_name, flight_direction="DESCENDING"),
            ]
        else:
            if args.asc_stack_name or args.dsc_stack_name:
                raise ValueError(
                    "--asc-stack-name/--dsc-stack-name require --dual-track."
                )
            direction_suffix = "asc" if args.flight_direction == "ASCENDING" else "dsc"
            stack_default = f"{project_name}_s1_{direction_suffix}_t000"
            stack_name = validate_slug(args.stack_name or stack_default, "stack-name")
            stacks = [StackSpec(name=stack_name, flight_direction=args.flight_direction)]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    target_root = Path(args.target_root)
    target_root = target_root if target_root.is_absolute() else (repo_root / target_root)
    target_project = target_root / project_name

    if target_project.exists():
        if not args.force:
            print(
                f"Target already exists: {target_project}\n"
                "Use --force to replace it.",
                file=sys.stderr,
            )
            return 2
        shutil.rmtree(target_project)

    target_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_root, target_project)

    stack_template_dir = target_project / "insar" / "example_stack_name"
    if not stack_template_dir.exists():
        print(f"Template stack folder missing: {stack_template_dir}", file=sys.stderr)
        return 2

    if len(stacks) == 1:
        stack_template_dir.rename(target_project / "insar" / stacks[0].name)
    else:
        asc_stack_dir = target_project / "insar" / stacks[0].name
        dsc_stack_dir = target_project / "insar" / stacks[1].name
        stack_template_dir.rename(asc_stack_dir)
        shutil.copytree(asc_stack_dir, dsc_stack_dir)

    try:
        project_root_value = target_project.relative_to(repo_root).as_posix()
    except ValueError:
        project_root_value = str(target_project)

    replacements = {
        "__PROJECT_NAME__": project_name,
        "__SITE_NAME__": site_name,
        "__PROJECT_ROOT__": project_root_value,
    }
    changed_files = replace_placeholders(target_project, replacements)
    for stack in stacks:
        changed_files += replace_placeholders(
            target_project / "insar" / stack.name,
            {"__STACK_NAME__": stack.name},
        )

    asc_stack_name = stacks[0].name
    dsc_stack_name = stacks[1].name if len(stacks) > 1 else stacks[0].name
    asc_velocity_file = (
        f"{project_root_value}/insar/{asc_stack_name}/stack/dolphin/timeseries/velocity.tif"
    )
    dsc_velocity_file = (
        f"{project_root_value}/insar/{dsc_stack_name}/stack/dolphin/timeseries/velocity.tif"
    )

    config_paths: list[str] = []
    for stack in stacks:
        config_path = (
            target_project / "insar" / stack.name / "config" / "processing_configuration.toml"
        )
        target_grid = "asc" if stack.flight_direction == "ASCENDING" else "dsc"
        update_stack_config(
            config_path=config_path,
            flight_direction=stack.flight_direction,
            asc_velocity_file=asc_velocity_file,
            dsc_velocity_file=dsc_velocity_file,
            target_grid=target_grid,
        )
        config_paths.append(
            f"{project_root_value}/insar/{stack.name}/config/processing_configuration.toml"
        )

    update_project_readme(
        readme_path=target_project / "README.md",
        project_name=project_name,
        project_root_value=project_root_value,
        stacks=stacks,
    )

    print(f"Project created: {target_project}")
    if len(stacks) == 1:
        print(f"Stack name: {stacks[0].name} ({stacks[0].flight_direction})")
    else:
        print("Stacks:")
        for stack in stacks:
            print(f"  - {stack.name} ({stack.flight_direction})")
    print(f"Site name: {site_name}")
    print(f"Files updated from placeholders: {changed_files}")
    print("\nNext:")
    print(
        f"1. Replace AOI polygon in {project_root_value}/aux/bbox.kml "
        "(Google Earth Pro -> Save Place As -> KML, WGS84 lon/lat EPSG:4326)."
    )
    if len(config_paths) == 1:
        print(f"2. Edit {config_paths[0]} (dates, orbit, selection).")
        print(
            "3. Run discovery/search helpers, then the main pipeline with "
            f"--config {config_paths[0]}."
        )
    else:
        print("2. Edit both stack configs (dates, orbit, reference date):")
        for cfg in config_paths:
            print(f"   - {cfg}")
        print("3. Run discovery/search/pipeline once per stack with matching --config.")
        print(
            f"4. After both Dolphin runs, run decomposition with --config {config_paths[0]}"
            " (or the DSC config)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
