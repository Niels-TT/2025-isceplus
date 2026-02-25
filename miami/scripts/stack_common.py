#!/usr/bin/env python3
from __future__ import annotations

import csv
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


def read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def resolve_path(repo_root: Path, path_value: str) -> Path:
    p = Path(path_value)
    return p if p.is_absolute() else (repo_root / p).resolve()


def parse_kml_points(kml_path: Path) -> list[tuple[float, float]]:
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
    points = parse_kml_points(kml_path)
    if points[0] != points[-1]:
        points = [*points, points[0]]
    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in points) + "))"


def kml_bbox(kml_path: Path) -> tuple[float, float, float, float]:
    points = parse_kml_points(kml_path)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def buffer_bbox(
    bbox: tuple[float, float, float, float], buffer_deg: float
) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = bbox
    return (
        max(-180.0, xmin - buffer_deg),
        max(-90.0, ymin - buffer_deg),
        min(180.0, xmax + buffer_deg),
        min(90.0, ymax + buffer_deg),
    )


def read_scene_rows(scene_csv: Path) -> list[dict[str, str]]:
    with scene_csv.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def iter_scene_filenames(rows: Iterable[dict[str, str]]) -> Iterable[tuple[str, str]]:
    for row in rows:
        scene_name = row.get("sceneName", "").strip()
        url = row.get("url", "").strip()
        filename = Path(urlparse(url).path).name
        if not filename:
            filename = f"{scene_name}.zip"
        yield scene_name, filename


def iso_to_yyyymmdd(timestamp: str) -> str:
    # Expected ASF timestamp format example: 2015-09-21T23:27:37.000Z
    return timestamp[:10].replace("-", "")
