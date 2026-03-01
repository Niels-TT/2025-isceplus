#!/usr/bin/env python3
"""Create a new project workspace from the reusable example scaffold.

Why:
    Keep project setup consistent so all pipeline scripts can run with only a
    project-specific config path and AOI KML.
"""

from __future__ import annotations

import argparse
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
        stack_default = f"{project_name}_s1_asc_t000"
        stack_name = validate_slug(args.stack_name or stack_default, "stack-name")
        site_name = validate_slug(args.site_name or project_name, "site-name")
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
    stack_dir = target_project / "insar" / stack_name
    if not stack_template_dir.exists():
        print(f"Template stack folder missing: {stack_template_dir}", file=sys.stderr)
        return 2
    stack_template_dir.rename(stack_dir)

    try:
        project_root_value = target_project.relative_to(repo_root).as_posix()
    except ValueError:
        project_root_value = str(target_project)

    replacements = {
        "__PROJECT_NAME__": project_name,
        "__STACK_NAME__": stack_name,
        "__SITE_NAME__": site_name,
        "__PROJECT_ROOT__": project_root_value,
    }
    changed_files = replace_placeholders(target_project, replacements)

    config_rel = f"{project_root_value}/insar/{stack_name}/config/processing_configuration.toml"
    print(f"Project created: {target_project}")
    print(f"Stack name: {stack_name}")
    print(f"Site name: {site_name}")
    print(f"Files updated from placeholders: {changed_files}")
    print("\nNext:")
    print(
        f"1. Replace AOI polygon in {project_root_value}/aux/bbox.kml "
        "(Google Earth Pro -> Save Place As -> KML)."
    )
    print(f"2. Edit {config_rel} (dates, orbit, selection).")
    print(
        "3. Run discovery/search helpers, then the main pipeline with "
        f"--config {config_rel}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
