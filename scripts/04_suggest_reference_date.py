#!/usr/bin/env python3
"""Suggest a reference date from searched stack dates.

Why:
    A good reference date should balance:
    - temporal support (neighbors / edge distance),
    - perpendicular-baseline centering across the selected stack.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


def read_toml(path: Path) -> dict[str, Any]:
    """Read TOML config from disk."""
    with path.open("rb") as f:
        return tomllib.load(f)


def resolve_path(repo_root: Path, value: str) -> Path:
    """Resolve a path relative to repo root when needed."""
    p = Path(value)
    return p if p.is_absolute() else (repo_root / p).resolve()


def parse_dates_from_csv(path: Path) -> list[date]:
    """Read unique acquisition dates from a scene CSV.

    Args:
        path: Scene CSV path (requires `startTime` column).

    Returns:
        Sorted unique date list.
    """
    values: set[date] = set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            start = str(row.get("startTime", "")).strip()
            if len(start) < 10:
                continue
            values.add(datetime.strptime(start[:10], "%Y-%m-%d").date())
    return sorted(values)


@dataclass
class Candidate:
    """Reference-date candidate scoring summary."""

    value: date
    reference_scene: str
    neighbors_within_window: int
    max_edge_days: int
    edge_balance_days: int
    center_index_distance: int
    baseline_scene_count: int
    median_abs_perpendicular_baseline_m: float | None
    max_abs_perpendicular_baseline_m: float | None
    mean_perpendicular_baseline_m: float | None


def parse_scene_rows(path: Path) -> list[dict[str, str]]:
    """Read scene metadata rows from CSV."""
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_dates_from_rows(rows: list[dict[str, str]]) -> list[date]:
    """Read unique acquisition dates from scene rows."""
    values: set[date] = set()
    for row in rows:
        start = str(row.get("startTime", "")).strip()
        if len(start) < 10:
            continue
        values.add(datetime.strptime(start[:10], "%Y-%m-%d").date())
    return sorted(values)


def infer_geojson_path_from_scenes_csv(scenes_csv: Path) -> Path:
    """Infer default results.geojson path from scenes.csv path layout."""
    # Expected:
    # .../search/products/scenes.csv -> .../search/raw/results.geojson
    return scenes_csv.parent.parent / "raw" / "results.geojson"


def parse_scene_file_id_map(path: Path) -> dict[str, str]:
    """Parse sceneName -> fileID mapping from search results GeoJSON."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    out: dict[str, str] = {}
    for feature in payload.get("features", []):
        props = feature.get("properties", {}) or {}
        scene_name = str(props.get("sceneName", "")).strip()
        file_id = str(props.get("fileID", "")).strip()
        if scene_name and file_id:
            out[scene_name] = file_id
    return out


def fetch_relative_perp_baselines(
    seed_reference_id: str,
) -> dict[str, float]:
    """Fetch perpendicular baselines relative to one seed reference scene."""
    try:
        import asf_search as asf  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency: asf_search. Activate the project conda environment "
            "(for example: conda activate isce3-feb)."
        ) from exc

    stack = asf.stack_from_id(seed_reference_id)
    out: dict[str, float] = {}
    for product in stack:
        scene_name = str(product.properties.get("sceneName", "")).strip()
        perp = product.properties.get("perpendicularBaseline")
        if not scene_name or perp is None:
            continue
        try:
            out[scene_name] = float(perp)
        except (TypeError, ValueError):
            continue
    return out


def score_candidates(
    *,
    rows: list[dict[str, str]],
    dates: list[date],
    window_days: int,
    relative_perp_by_scene: dict[str, float],
) -> list[Candidate]:
    """Score candidate dates using baseline-aware + temporal heuristics.

    Ranking priorities (best first):
    1) Smaller median absolute perpendicular baseline over selected scenes.
    2) Smaller max absolute perpendicular baseline.
    3) Better baseline balance around zero (smaller abs(mean baseline)).
    4) More temporal neighbors within +/- window_days.
    5) Smaller edge distance / edge imbalance.
    6) Closer to temporal center index.
    """
    if not dates:
        return []

    scenes_by_date: dict[date, list[str]] = {}
    selected_scenes: set[str] = set()
    for row in rows:
        scene_name = str(row.get("sceneName", "")).strip()
        start = str(row.get("startTime", "")).strip()
        if len(start) < 10 or not scene_name:
            continue
        d = datetime.strptime(start[:10], "%Y-%m-%d").date()
        scenes_by_date.setdefault(d, []).append(scene_name)
        selected_scenes.add(scene_name)

    selected_rel = {
        scene: rel
        for scene, rel in relative_perp_by_scene.items()
        if scene in selected_scenes
    }

    center_idx = len(dates) // 2
    first = dates[0]
    last = dates[-1]

    out: list[Candidate] = []
    for idx, d in enumerate(dates):
        neighbors = sum(1 for x in dates if abs((x - d).days) <= window_days) - 1
        left = (d - first).days
        right = (last - d).days

        date_scenes = sorted(scenes_by_date.get(d, []))
        # Prefer a scene with available baseline as reference anchor.
        reference_scene = next(
            (name for name in date_scenes if name in selected_rel),
            date_scenes[0] if date_scenes else "",
        )
        ref_rel = selected_rel.get(reference_scene)

        baseline_scene_count = 0
        med_abs: float | None = None
        max_abs: float | None = None
        mean_perp: float | None = None
        if ref_rel is not None:
            centered = [rel - ref_rel for rel in selected_rel.values()]
            if centered:
                baseline_scene_count = len(centered)
                abs_values = [abs(v) for v in centered]
                med_abs = float(statistics.median(abs_values))
                max_abs = float(max(abs_values))
                mean_perp = float(statistics.mean(centered))

        out.append(
            Candidate(
                value=d,
                reference_scene=reference_scene,
                neighbors_within_window=max(0, neighbors),
                max_edge_days=max(left, right),
                edge_balance_days=abs(left - right),
                center_index_distance=abs(idx - center_idx),
                baseline_scene_count=baseline_scene_count,
                median_abs_perpendicular_baseline_m=med_abs,
                max_abs_perpendicular_baseline_m=max_abs,
                mean_perpendicular_baseline_m=mean_perp,
            )
        )

    def rank_key(c: Candidate) -> tuple:
        has_baseline = c.median_abs_perpendicular_baseline_m is not None
        return (
            0 if has_baseline else 1,
            c.median_abs_perpendicular_baseline_m
            if c.median_abs_perpendicular_baseline_m is not None
            else float("inf"),
            c.max_abs_perpendicular_baseline_m
            if c.max_abs_perpendicular_baseline_m is not None
            else float("inf"),
            abs(c.mean_perpendicular_baseline_m)
            if c.mean_perpendicular_baseline_m is not None
            else float("inf"),
            -c.neighbors_within_window,
            c.max_edge_days,
            c.edge_balance_days,
            c.center_index_distance,
            c.value.isoformat(),
        )

    out.sort(key=rank_key)
    return out


def main() -> int:
    """Parse CLI args and print/write reference date suggestions."""
    parser = argparse.ArgumentParser(
        description="Suggest a robust reference date from searched scene dates."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root directory (default: current directory).",
    )
    parser.add_argument(
        "--config",
        default="",
        help="Optional stack TOML config path (used to locate scenes.csv).",
    )
    parser.add_argument(
        "--scenes-csv",
        default="",
        help="Scene CSV path override (defaults to outputs.root + outputs.metadata_csv).",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=180,
        help="Neighbor window in days for density scoring (default: 180).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of top candidates to print (default: 5).",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional JSON output path for ranked suggestions.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    cfg: dict[str, Any] = {}
    config_path: Path | None = None
    scenes_csv: Path | None = None

    if args.config:
        config_path = resolve_path(repo_root, args.config)
        cfg = read_toml(config_path)

    if args.scenes_csv:
        scenes_csv = resolve_path(repo_root, args.scenes_csv)
    elif cfg:
        outputs_cfg = cfg.get("outputs", {})
        root_value = str(outputs_cfg.get("root", "")).strip()
        meta_value = str(outputs_cfg.get("metadata_csv", "")).strip()
        if root_value and meta_value:
            scenes_csv = resolve_path(repo_root, root_value) / meta_value

    if scenes_csv is None:
        print(
            "Missing scenes CSV. Run scripts/03_search_s1_stack.py first, then re-run this command "
            "with --config (or pass --scenes-csv explicitly).",
            file=sys.stderr,
        )
        return 2
    if not scenes_csv.exists():
        print(
            f"Missing scenes CSV: {scenes_csv}\n"
            "Run scripts/03_search_s1_stack.py first to generate search/products/scenes.csv.",
            file=sys.stderr,
        )
        return 2

    rows = parse_scene_rows(scenes_csv)
    dates = parse_dates_from_rows(rows)
    if len(dates) < 2:
        print(
            f"Need at least 2 acquisition dates, found {len(dates)} in {scenes_csv}.",
            file=sys.stderr,
        )
        return 2

    scene_names = sorted(
        {
            str(row.get("sceneName", "")).strip()
            for row in rows
            if str(row.get("sceneName", "")).strip()
        }
    )
    if not scene_names:
        print(
            f"No valid sceneName values found in {scenes_csv}.",
            file=sys.stderr,
        )
        return 2

    scene_to_file_id: dict[str, str] = {}
    if cfg:
        outputs_cfg = cfg.get("outputs", {})
        root_value = str(outputs_cfg.get("root", "")).strip()
        geojson_value = str(outputs_cfg.get("geojson", "")).strip()
        if root_value and geojson_value:
            geojson_path = resolve_path(repo_root, root_value) / geojson_value
            scene_to_file_id = parse_scene_file_id_map(geojson_path)

    if not scene_to_file_id:
        fallback_geojson = infer_geojson_path_from_scenes_csv(scenes_csv)
        scene_to_file_id = parse_scene_file_id_map(fallback_geojson)

    seed_scene = scene_names[0]
    seed_reference_id = scene_to_file_id.get(seed_scene, f"{seed_scene}-SLC")
    try:
        rel_perp = fetch_relative_perp_baselines(seed_reference_id)
    except Exception as exc:  # noqa: BLE001
        print(
            f"Failed to retrieve perpendicular baselines from ASF for reference {seed_reference_id}: {exc}",
            file=sys.stderr,
        )
        return 2

    ranked = score_candidates(
        rows=rows,
        dates=dates,
        window_days=max(1, args.window_days),
        relative_perp_by_scene=rel_perp,
    )
    recommended = ranked[0]
    earliest = dates[0]
    center = dates[len(dates) // 2]

    print(f"Scenes CSV: {scenes_csv}")
    if config_path is not None:
        print(f"Config: {config_path}")
    print(f"Unique dates: {len(dates)}")
    print(f"Date span: {dates[0].isoformat()} -> {dates[-1].isoformat()}")
    print(f"Window days for neighbor score: {max(1, args.window_days)}")
    print(f"Baseline anchor scene: {seed_scene}")
    print(f"Baseline anchor reference_id: {seed_reference_id}")
    print()
    print("Recommended reference date (baseline + temporal heuristic):")
    rec_med = (
        f"{recommended.median_abs_perpendicular_baseline_m:.1f}"
        if recommended.median_abs_perpendicular_baseline_m is not None
        else "n/a"
    )
    rec_max = (
        f"{recommended.max_abs_perpendicular_baseline_m:.1f}"
        if recommended.max_abs_perpendicular_baseline_m is not None
        else "n/a"
    )
    print(
        f"  {recommended.value.isoformat()} "
        f"(ref_scene={recommended.reference_scene}, "
        f"median_abs_perp_m={rec_med}, "
        f"max_abs_perp_m={rec_max}, "
        f"neighbors={recommended.neighbors_within_window}, "
        f"max_edge_days={recommended.max_edge_days}, "
        f"edge_balance_days={recommended.edge_balance_days})"
    )
    print(f"Alternative 'earliest' date: {earliest.isoformat()}")
    print(f"Alternative 'center' date: {center.isoformat()}")
    print()

    top_k = max(1, args.top_k)
    print(f"Top {min(top_k, len(ranked))} candidates:")
    print(
        "rank  date        med|Bperp|m  max|Bperp|m  neighbors  max_edge_days  edge_balance_days"
    )
    print(
        "----  ----------  -----------  -----------  ---------  -------------  -----------------"
    )
    for i, cand in enumerate(ranked[:top_k], start=1):
        med_abs = (
            f"{cand.median_abs_perpendicular_baseline_m:>11.1f}"
            if cand.median_abs_perpendicular_baseline_m is not None
            else "        n/a"
        )
        max_abs = (
            f"{cand.max_abs_perpendicular_baseline_m:>11.1f}"
            if cand.max_abs_perpendicular_baseline_m is not None
            else "        n/a"
        )
        print(
            f"{i:>4}  {cand.value.isoformat():<10}  "
            f"{med_abs}  {max_abs}  "
            f"{cand.neighbors_within_window:>9}  {cand.max_edge_days:>13}  "
            f"{cand.edge_balance_days:>17}"
        )

    output_json: Path | None = None
    if args.output_json:
        output_json = resolve_path(repo_root, args.output_json)
    else:
        output_json = scenes_csv.parent / "reference_date_suggestions.json"

    payload = {
        "scenes_csv": str(scenes_csv),
        "date_count": len(dates),
        "first_date": dates[0].isoformat(),
        "last_date": dates[-1].isoformat(),
        "window_days": max(1, args.window_days),
        "baseline_anchor_scene": seed_scene,
        "baseline_anchor_reference_id": seed_reference_id,
        "recommended_reference_date": recommended.value.isoformat(),
        "earliest_reference_date": earliest.isoformat(),
        "center_reference_date": center.isoformat(),
        "ranking": [
            {
                "date": c.value.isoformat(),
                "reference_scene": c.reference_scene,
                "baseline_scene_count": c.baseline_scene_count,
                "median_abs_perpendicular_baseline_m": c.median_abs_perpendicular_baseline_m,
                "max_abs_perpendicular_baseline_m": c.max_abs_perpendicular_baseline_m,
                "mean_perpendicular_baseline_m": c.mean_perpendicular_baseline_m,
                "neighbors_within_window": c.neighbors_within_window,
                "max_edge_days": c.max_edge_days,
                "edge_balance_days": c.edge_balance_days,
                "center_index_distance": c.center_index_distance,
            }
            for c in ranked
        ],
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote JSON: {output_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
