#!/usr/bin/env python3
"""Shared helpers for stack search, download, and preprocessing scripts."""

from __future__ import annotations

import csv
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
# Empty default means: auto-discover when possible, otherwise require --config.
DEFAULT_STACK_CONFIG_REL = ""
STACK_CONFIG_GLOBS = (
    "projects/*/insar/*/config/processing_configuration.toml",
    "miami/insar/*/config/processing_configuration.toml",
    "example_project/insar/*/config/processing_configuration.toml",
)


def read_toml(path: Path) -> dict:
    """Load a TOML file into a dictionary.

    Args:
        path: Path to the TOML file.

    Returns:
        Parsed TOML content.
    """
    with path.open("rb") as f:
        return tomllib.load(f)


def resolve_path(repo_root: Path, path_value: str) -> Path:
    """Resolve a config path relative to repository root when needed.

    Args:
        repo_root: Repository root directory.
        path_value: Absolute or relative path string from config/CLI.

    Returns:
        Absolute resolved path.
    """
    p = Path(path_value)
    return p if p.is_absolute() else (repo_root / p).resolve()


def discover_stack_configs(repo_root: Path) -> list[Path]:
    """Discover candidate stack config files under known project folders.

    Args:
        repo_root: Repository root directory.

    Returns:
        Sorted unique absolute paths to discovered stack config files.
    """
    seen: set[Path] = set()
    discovered: list[Path] = []
    for pattern in STACK_CONFIG_GLOBS:
        for path in sorted(repo_root.glob(pattern)):
            resolved = path.resolve()
            if resolved in seen or not resolved.is_file():
                continue
            seen.add(resolved)
            discovered.append(resolved)
    return discovered


def resolve_stack_config(repo_root: Path, config_value: str) -> Path:
    """Resolve stack config path from CLI value or auto-discovery.

    Why:
        Avoid implicit coupling to a single hard-coded Miami project path.

    Args:
        repo_root: Repository root directory.
        config_value: Optional CLI config path.

    Returns:
        Absolute stack config path.

    Raises:
        FileNotFoundError: If no candidate config exists.
        RuntimeError: If multiple candidate configs exist and none was selected.
    """
    text = str(config_value).strip() if config_value is not None else ""
    if text:
        path = resolve_path(repo_root, text)
        if not path.exists():
            raise FileNotFoundError(f"Stack config does not exist: {path}")
        return path

    candidates = discover_stack_configs(repo_root)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(
            "No processing_configuration.toml found. Pass --config explicitly."
        )
    options = "\n".join(f"  - {p}" for p in candidates)
    raise RuntimeError(
        "Multiple stack configs found. Pass --config explicitly.\nCandidates:\n"
        f"{options}"
    )


def infer_stack_root(config_path: Path) -> Path:
    """Infer stack root directory from stack config path.

    Expected layout:
        <project>/insar/<stack_name>/config/processing_configuration.toml

    Args:
        config_path: Absolute stack config path.

    Returns:
        Inferred stack root directory path (`.../insar/<stack_name>`).
    """
    return config_path.resolve().parent.parent


def parse_kml_points(kml_path: Path) -> list[tuple[float, float]]:
    """Parse lon/lat polygon points from a KML coordinate element.

    Args:
        kml_path: Path to KML file containing polygon coordinates.

    Returns:
        List of (lon, lat) tuples.

    Raises:
        ValueError: If no polygon coordinates exist or polygon is invalid.
    """
    root = ET.parse(kml_path).getroot()
    coordinates = root.find(".//kml:coordinates", KML_NS)
    if coordinates is None or not coordinates.text:
        raise ValueError(f"No polygon coordinates found in KML: {kml_path}")

    points: list[tuple[float, float]] = []
    for token in coordinates.text.strip().split():
        lon, lat, *_ = token.split(",")
        points.append((float(lon), float(lat)))
    if len(points) < 4:
        raise ValueError(f"Invalid polygon in KML (too few points): {kml_path}")
    return points


def parse_kml_to_wkt(kml_path: Path) -> str:
    """Convert a KML polygon to WKT POLYGON text.

    Args:
        kml_path: Path to KML file.

    Returns:
        WKT polygon string.
    """
    points = parse_kml_points(kml_path)
    if points[0] != points[-1]:
        points = [*points, points[0]]
    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in points) + "))"


def kml_bbox(kml_path: Path) -> tuple[float, float, float, float]:
    """Compute bounding box from KML polygon coordinates.

    Args:
        kml_path: Path to KML file.

    Returns:
        Bounding box as (xmin, ymin, xmax, ymax).
    """
    points = parse_kml_points(kml_path)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def buffer_bbox(
    bbox: tuple[float, float, float, float], buffer_deg: float
) -> tuple[float, float, float, float]:
    """Expand a bounding box by degrees and clamp to geographic limits.

    Args:
        bbox: Bounding box as (xmin, ymin, xmax, ymax).
        buffer_deg: Buffer size in degrees.

    Returns:
        Buffered and clamped bounding box.
    """
    xmin, ymin, xmax, ymax = bbox
    return (
        max(-180.0, xmin - buffer_deg),
        max(-90.0, ymin - buffer_deg),
        min(180.0, xmax + buffer_deg),
        min(90.0, ymax + buffer_deg),
    )


def read_scene_rows(scene_csv: Path) -> list[dict[str, str]]:
    """Load scene metadata rows from CSV.

    Args:
        scene_csv: Path to scenes CSV file.

    Returns:
        List of CSV rows as dictionaries.
    """
    with scene_csv.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def iter_scene_filenames(rows: Iterable[dict[str, str]]) -> Iterable[tuple[str, str]]:
    """Yield scene name and output ZIP filename pairs from metadata rows.

    Args:
        rows: Iterable of scene metadata rows.

    Yields:
        Tuples of (scene_name, filename).
    """
    for row in rows:
        scene_name = row.get("sceneName", "").strip()
        url = row.get("url", "").strip()
        filename = Path(urlparse(url).path).name
        if not filename:
            filename = f"{scene_name}.zip"
        yield scene_name, filename


def iso_to_yyyymmdd(timestamp: str) -> str:
    """Convert ASF-like ISO timestamp to YYYYMMDD.

    Args:
        timestamp: Timestamp string, e.g. 2015-09-21T23:27:37.000Z.

    Returns:
        Date string formatted as YYYYMMDD.
    """
    # Expected ASF timestamp format example: 2015-09-21T23:27:37.000Z
    return timestamp[:10].replace("-", "")
