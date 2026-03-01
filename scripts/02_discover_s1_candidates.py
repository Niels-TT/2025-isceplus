#!/usr/bin/env python3
"""Discover Sentinel-1 acquisition geometries over an AOI.

Why:
    Before fixing a single orbit/direction in project config, quickly inspect
    which geometry groups provide enough temporal coverage for stack creation.

Outputs:
    - Ranked geometry table (terminal + optional CSV/JSON)
    - Optional map PNG with AOI and all discovered stack footprints
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tomllib
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


@dataclass
class GeometrySummary:
    """Aggregated geometry statistics from ASF search results."""

    flight_direction: str
    relative_orbit: int
    frame_number: str
    beam_mode: str
    polarization: str
    scene_count: int
    unique_dates: int
    first_date: str
    last_date: str
    total_gb_decimal: float


def read_toml(path: Path) -> dict[str, Any]:
    """Read TOML config from disk."""
    with path.open("rb") as f:
        return tomllib.load(f)


def resolve_path(repo_root: Path, value: str) -> Path:
    """Resolve a path relative to repo root when needed."""
    p = Path(value)
    return p if p.is_absolute() else (repo_root / p).resolve()


def parse_kml_points(kml_path: Path) -> list[tuple[float, float]]:
    """Parse lon/lat polygon points from a KML coordinate element."""
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
    if points[0] != points[-1]:
        points = [*points, points[0]]
    return points


def parse_kml_to_wkt(kml_path: Path) -> str:
    """Convert polygon coordinates in KML to WKT polygon."""
    points = parse_kml_points(kml_path)
    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in points) + "))"


def enum_or_fail(enum_obj: Any, value: str, field_name: str):
    """Resolve enum name from asf_search enum container."""
    try:
        return getattr(enum_obj, value)
    except AttributeError as exc:
        raise ValueError(f"Invalid {field_name}: {value}") from exc


def to_int(value: Any, default: int = 0) -> int:
    """Convert value to integer with fallback."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def group_key_from_props(props: dict[str, Any]) -> tuple[str, int, str]:
    """Build geometry grouping key from ASF feature properties."""
    flight = str(props.get("flightDirection", "UNKNOWN")).upper()
    rel_orbit = to_int(
        props.get("pathNumber", props.get("relativeOrbit", 0)),
        default=0,
    )
    frame = str(props.get("frameNumber", ""))
    return (flight, rel_orbit, frame)


def build_summaries(results: list[Any]) -> list[GeometrySummary]:
    """Aggregate search results by geometry group.

    Group key:
        (flight_direction, relative_orbit, frame_number)
    """
    groups: dict[tuple[str, int, str], dict[str, Any]] = defaultdict(
        lambda: {
            "dates": set(),
            "scene_count": 0,
            "bytes": 0,
            "beam_mode": "",
            "polarization": set(),
        }
    )

    for item in results:
        props = item.properties
        key = group_key_from_props(props)
        g = groups[key]
        g["scene_count"] += 1
        start_time = str(props.get("startTime", ""))
        if len(start_time) >= 10:
            g["dates"].add(start_time[:10])
        g["beam_mode"] = str(props.get("beamModeType", ""))
        pol = str(props.get("polarization", "")).strip()
        if pol:
            g["polarization"].add(pol)
        g["bytes"] += to_int(props.get("bytes", 0), default=0)

    out: list[GeometrySummary] = []
    for (flight, rel_orbit, frame), g in groups.items():
        dates = sorted(g["dates"])
        out.append(
            GeometrySummary(
                flight_direction=flight,
                relative_orbit=rel_orbit,
                frame_number=frame,
                beam_mode=g["beam_mode"],
                polarization=",".join(sorted(g["polarization"])) if g["polarization"] else "",
                scene_count=int(g["scene_count"]),
                unique_dates=len(dates),
                first_date=dates[0] if dates else "",
                last_date=dates[-1] if dates else "",
                total_gb_decimal=round(float(g["bytes"]) / 1e9, 2),
            )
        )

    out.sort(
        key=lambda s: (
            -s.unique_dates,
            -s.scene_count,
            s.flight_direction,
            s.relative_orbit,
            s.frame_number,
        )
    )
    return out


def print_table(rows: list[GeometrySummary], max_rows: int) -> None:
    """Print ranked geometry rows as a compact table."""
    shown = rows[:max_rows] if max_rows > 0 else rows
    header = (
        f"{'rank':>4}  {'dir':<9}  {'orbit':>5}  {'frame':>6}  "
        f"{'dates':>5}  {'scenes':>6}  {'first':<10}  {'last':<10}  {'GB':>7}"
    )
    print(header)
    print("-" * len(header))
    for idx, row in enumerate(shown, start=1):
        print(
            f"{idx:>4}  {row.flight_direction:<9}  {row.relative_orbit:>5}  "
            f"{row.frame_number:>6}  {row.unique_dates:>5}  {row.scene_count:>6}  "
            f"{row.first_date:<10}  {row.last_date:<10}  {row.total_gb_decimal:>7.2f}"
        )


def write_csv_rows(path: Path, rows: list[GeometrySummary]) -> None:
    """Write geometry summary table to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "flight_direction",
                "relative_orbit",
                "frame_number",
                "beam_mode",
                "polarization",
                "scene_count",
                "unique_dates",
                "first_date",
                "last_date",
                "total_gb_decimal",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.flight_direction,
                    row.relative_orbit,
                    row.frame_number,
                    row.beam_mode,
                    row.polarization,
                    row.scene_count,
                    row.unique_dates,
                    row.first_date,
                    row.last_date,
                    f"{row.total_gb_decimal:.2f}",
                ]
            )


def iter_polygons(geometry: Any):
    """Yield polygon members from a shapely geometry."""
    geom_type = geometry.geom_type
    if geom_type == "Polygon":
        yield geometry
        return
    if geom_type == "MultiPolygon":
        for poly in geometry.geoms:
            yield poly
        return
    if geom_type == "GeometryCollection":
        for sub in geometry.geoms:
            yield from iter_polygons(sub)


def draw_group_geometry(ax: Any, geometry: Any, color: Any) -> None:
    """Draw one grouped stack geometry with halo and subtle fill."""
    for poly in iter_polygons(geometry):
        x, y = poly.exterior.xy
        ax.fill(x, y, color=color, alpha=0.08, zorder=3)
        ax.plot(x, y, color="white", linewidth=3.6, alpha=0.95, zorder=4)
        ax.plot(x, y, color=color, linewidth=1.8, alpha=0.95, zorder=5)


def write_geometry_map(
    *,
    features: list[dict[str, Any]],
    summaries: list[GeometrySummary],
    aoi_points: list[tuple[float, float]],
    out_png: Path,
    dpi: int,
) -> dict[str, Any]:
    """Write a candidate-stack footprint map against AOI.

    Args:
        features: ASF GeoJSON feature list.
        summaries: Ranked group summaries (already filtered).
        aoi_points: AOI polygon points (lon, lat).
        out_png: Output PNG path.
        dpi: Figure DPI.

    Returns:
        Small map metadata dictionary for JSON reporting.
    """
    try:
        from shapely.geometry import shape
        from shapely.ops import unary_union
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency 'shapely' required for map generation."
        ) from exc

    summary_map: dict[tuple[str, int, str], GeometrySummary] = {
        (s.flight_direction, s.relative_orbit, s.frame_number): s for s in summaries
    }
    grouped: dict[tuple[str, int, str], list[Any]] = defaultdict(list)

    for feature in features:
        props = feature.get("properties", {}) or {}
        key = group_key_from_props(props)
        if key not in summary_map:
            continue
        geometry_data = feature.get("geometry")
        if not geometry_data:
            continue
        geom = shape(geometry_data)
        if geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
            if geom.is_empty:
                continue
        grouped[key].append(geom)

    if not grouped:
        raise RuntimeError("No valid geometries found for footprint map.")

    combined: dict[tuple[str, int, str], Any] = {
        key: unary_union(geoms) for key, geoms in grouped.items()
    }

    fig, ax = plt.subplots(figsize=(11.69, 8.27), dpi=110)
    ax.set_facecolor("#f9fafc")

    # Plot candidates in ranked order for consistent legend coloring.
    cmap = plt.get_cmap("tab20")
    handles: list[Any] = []
    legend_labels: list[str] = []
    all_bounds: list[tuple[float, float, float, float]] = []

    for idx, summary in enumerate(summaries):
        key = (summary.flight_direction, summary.relative_orbit, summary.frame_number)
        geom = combined.get(key)
        if geom is None:
            continue
        color = cmap(idx % 20)
        draw_group_geometry(ax, geom, color)
        all_bounds.append(geom.bounds)
        handles.append(Line2D([0], [0], color=color, linewidth=2.5))
        legend_labels.append(
            f"{summary.flight_direction} r{summary.relative_orbit} f{summary.frame_number} "
            f"({summary.unique_dates} dates)"
        )

    aoi_x = [p[0] for p in aoi_points]
    aoi_y = [p[1] for p in aoi_points]
    ax.fill(aoi_x, aoi_y, color="#0077b6", alpha=0.12, zorder=8)
    ax.plot(aoi_x, aoi_y, color="white", linewidth=4.0, zorder=9)
    ax.plot(aoi_x, aoi_y, color="#0077b6", linewidth=2.2, zorder=10)

    handles.insert(
        0,
        Line2D(
            [0],
            [0],
            color="#0077b6",
            linewidth=2.2,
        ),
    )
    legend_labels.insert(0, "AOI")

    # Extent from all candidate groups + AOI.
    minx = min(min(b[0] for b in all_bounds), min(aoi_x))
    miny = min(min(b[1] for b in all_bounds), min(aoi_y))
    maxx = max(max(b[2] for b in all_bounds), max(aoi_x))
    maxy = max(max(b[3] for b in all_bounds), max(aoi_y))

    dx = maxx - minx
    dy = maxy - miny
    pad_x = max(dx * 0.08, 0.01)
    pad_y = max(dy * 0.08, 0.01)

    ax.set_xlim(minx - pad_x, maxx + pad_x)
    ax.set_ylim(miny - pad_y, maxy + pad_y)
    ax.set_aspect("equal", adjustable="box")

    ax.grid(color="#d9dee6", linestyle="--", linewidth=0.6, alpha=0.6)
    ax.set_xlabel("Longitude (deg)")
    ax.set_ylabel("Latitude (deg)")

    ax.set_title(
        "Sentinel-1 Candidate Stack Footprints vs AOI",
        fontsize=13,
        fontweight="bold",
        pad=12,
    )

    if handles:
        max_legend = 12
        shown_handles = handles[:max_legend]
        shown_labels = legend_labels[:max_legend]
        if len(handles) > max_legend:
            shown_handles.append(Line2D([0], [0], color="#666666", linewidth=0.0))
            shown_labels.append(f"... +{len(handles) - max_legend} more groups")
        leg = ax.legend(
            shown_handles,
            shown_labels,
            loc="lower right",
            frameon=True,
            fontsize=8.8,
            title="Geometry groups",
            title_fontsize=9.2,
        )
        leg.get_frame().set_facecolor("white")
        leg.get_frame().set_alpha(0.96)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=max(100, dpi), bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)

    return {
        "map_png": str(out_png),
        "group_count_mapped": len(combined),
        "aoi_vertex_count": len(aoi_points),
    }


def main() -> int:
    """Parse CLI args, run discovery search, and write ranked geometry options."""
    parser = argparse.ArgumentParser(
        description="Discover Sentinel-1 geometry candidates for a project AOI/time window."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory).",
    )
    parser.add_argument(
        "--config",
        default="",
        help="Optional stack TOML config path. Used for defaults when provided.",
    )
    parser.add_argument("--kml", default="", help="AOI KML path override.")
    parser.add_argument("--start", default="", help="Start datetime (ISO8601).")
    parser.add_argument("--end", default="", help="End datetime (ISO8601).")
    parser.add_argument("--platform", default="SENTINEL1", help="ASF platform enum name.")
    parser.add_argument(
        "--processing-level",
        default="SLC",
        help="ASF product type enum name.",
    )
    parser.add_argument("--beam-mode", default="IW", help="ASF beam mode enum name.")
    parser.add_argument(
        "--output-csv",
        default="",
        help="Output CSV path. Default: <outputs.root>/candidates/geometry_candidates.csv",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Output JSON summary path. Default: <outputs.root>/candidates/geometry_candidates.json",
    )
    parser.add_argument(
        "--output-map",
        default="",
        help="Output map PNG path. Default: <outputs.root>/candidates/geometry_candidates_map.png",
    )
    parser.add_argument(
        "--no-map",
        action="store_true",
        help="Disable map PNG creation.",
    )
    parser.add_argument(
        "--map-dpi",
        type=int,
        default=260,
        help="Map PNG resolution in DPI.",
    )
    parser.add_argument(
        "--min-unique-dates",
        type=int,
        default=5,
        help="Hide geometry groups with fewer than this many acquisition dates.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=20,
        help="Max rows printed to terminal (0 = all).",
    )
    args = parser.parse_args()

    try:
        import asf_search as asf  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        print(
            "Missing dependency: asf_search. Activate the project conda environment "
            "(for example: conda activate isce3-feb) before running discovery.",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(args.repo_root).resolve()
    cfg: dict[str, Any] = {}
    search_cfg: dict[str, Any] = {}
    output_root: Path | None = None

    if args.config:
        config_path = resolve_path(repo_root, args.config)
        cfg = read_toml(config_path)
        search_cfg = cfg.get("search", {})
        outputs_cfg = cfg.get("outputs", {})
        root_value = str(outputs_cfg.get("root", "")).strip()
        if root_value:
            output_root = resolve_path(repo_root, root_value)

    kml_value = args.kml or str(cfg.get("aoi", {}).get("kml", "")).strip()
    if not kml_value:
        print("Missing AOI KML. Pass --kml or provide aoi.kml in config.", file=sys.stderr)
        return 2
    kml_path = resolve_path(repo_root, kml_value)
    if not kml_path.exists():
        print(f"Missing KML file: {kml_path}", file=sys.stderr)
        return 2

    start = args.start or str(search_cfg.get("start", "")).strip()
    end = args.end or str(search_cfg.get("end", "")).strip()
    if not start or not end:
        print("Missing --start/--end (or search.start/search.end in config).", file=sys.stderr)
        return 2

    platform_name = args.platform or str(search_cfg.get("platform", "SENTINEL1"))
    processing_name = args.processing_level or str(search_cfg.get("processing_level", "SLC"))
    beam_name = args.beam_mode or str(search_cfg.get("beam_mode", "IW"))

    try:
        platform = enum_or_fail(asf.PLATFORM, platform_name, "platform")
        processing_level = enum_or_fail(asf.PRODUCT_TYPE, processing_name, "processing_level")
        beam_mode = enum_or_fail(asf.BEAMMODE, beam_name, "beam_mode")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    wkt = parse_kml_to_wkt(kml_path)
    aoi_points = parse_kml_points(kml_path)

    results = asf.search(
        platform=[platform],
        processingLevel=[processing_level],
        beamMode=[beam_mode],
        start=start,
        end=end,
        intersectsWith=wkt,
    )

    result_list = sorted(results, key=lambda item: item.properties.get("startTime", ""))
    summaries = [s for s in build_summaries(result_list) if s.unique_dates >= args.min_unique_dates]

    if args.output_csv:
        output_csv = resolve_path(repo_root, args.output_csv)
    elif output_root is not None:
        output_csv = output_root / "candidates" / "geometry_candidates.csv"
    else:
        output_csv = None

    if args.output_json:
        output_json = resolve_path(repo_root, args.output_json)
    elif output_root is not None:
        output_json = output_root / "candidates" / "geometry_candidates.json"
    else:
        output_json = None

    if args.output_map:
        output_map = resolve_path(repo_root, args.output_map)
    elif output_root is not None:
        output_map = output_root / "candidates" / "geometry_candidates_map.png"
    else:
        output_map = None

    print(f"AOI KML: {kml_path}")
    print(f"Search window: {start} -> {end}")
    print(f"Total matching scenes: {len(result_list)}")
    print(f"Geometry groups (after min-unique-dates={args.min_unique_dates}): {len(summaries)}")
    if summaries:
        print()
        print_table(summaries, max_rows=args.max_rows)
    else:
        print("No geometry groups met the minimum unique-date threshold.")

    if output_csv is not None:
        write_csv_rows(output_csv, summaries)
        print(f"\nWrote CSV: {output_csv}")

    map_meta: dict[str, Any] | None = None
    if not args.no_map and output_map is not None and summaries:
        try:
            full_geojson = results.geojson() if results else {"features": []}
            features = full_geojson.get("features", [])
            map_meta = write_geometry_map(
                features=features,
                summaries=summaries,
                aoi_points=aoi_points,
                out_png=output_map,
                dpi=max(100, args.map_dpi),
            )
            print(f"Wrote map PNG: {output_map}")
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] Could not generate map PNG: {exc}", file=sys.stderr)

    if output_json is not None:
        payload = {
            "aoi_kml": str(kml_path),
            "start": start,
            "end": end,
            "platform": platform_name,
            "processing_level": processing_name,
            "beam_mode": beam_name,
            "total_matching_scenes": len(result_list),
            "min_unique_dates": args.min_unique_dates,
            "map": map_meta,
            "groups": [row.__dict__ for row in summaries],
        }
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote JSON: {output_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
