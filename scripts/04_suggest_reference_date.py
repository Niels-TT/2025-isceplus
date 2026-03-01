#!/usr/bin/env python3
"""Suggest a reference date from searched stack dates.

Why:
    A good reference date should have dense temporal neighbors and avoid edge
    dates that can weaken inversion stability.
"""

from __future__ import annotations

import argparse
import csv
import json
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
    neighbors_within_window: int
    max_edge_days: int
    edge_balance_days: int
    center_index_distance: int


def score_candidates(dates: list[date], window_days: int) -> list[Candidate]:
    """Score all candidate reference dates using simple deterministic heuristics.

    Ranking priorities (best first):
    1) More neighbors within +/- window_days.
    2) Smaller max distance to stack edges.
    3) Better left/right edge balance.
    4) Closer to temporal center index.
    """
    if not dates:
        return []

    center_idx = len(dates) // 2
    first = dates[0]
    last = dates[-1]

    out: list[Candidate] = []
    for idx, d in enumerate(dates):
        neighbors = sum(1 for x in dates if abs((x - d).days) <= window_days) - 1
        left = (d - first).days
        right = (last - d).days
        out.append(
            Candidate(
                value=d,
                neighbors_within_window=max(0, neighbors),
                max_edge_days=max(left, right),
                edge_balance_days=abs(left - right),
                center_index_distance=abs(idx - center_idx),
            )
        )

    out.sort(
        key=lambda c: (
            -c.neighbors_within_window,
            c.max_edge_days,
            c.edge_balance_days,
            c.center_index_distance,
            c.value.isoformat(),
        )
    )
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
            "Missing scenes CSV. Pass --scenes-csv or provide --config with outputs.root/metadata_csv.",
            file=sys.stderr,
        )
        return 2
    if not scenes_csv.exists():
        print(f"Missing scenes CSV: {scenes_csv}", file=sys.stderr)
        return 2

    dates = parse_dates_from_csv(scenes_csv)
    if len(dates) < 2:
        print(
            f"Need at least 2 acquisition dates, found {len(dates)} in {scenes_csv}.",
            file=sys.stderr,
        )
        return 2

    ranked = score_candidates(dates, window_days=max(1, args.window_days))
    recommended = ranked[0]
    earliest = dates[0]
    center = dates[len(dates) // 2]

    print(f"Scenes CSV: {scenes_csv}")
    if config_path is not None:
        print(f"Config: {config_path}")
    print(f"Unique dates: {len(dates)}")
    print(f"Date span: {dates[0].isoformat()} -> {dates[-1].isoformat()}")
    print(f"Window days for neighbor score: {max(1, args.window_days)}")
    print()
    print("Recommended reference date (heuristic):")
    print(
        f"  {recommended.value.isoformat()} "
        f"(neighbors={recommended.neighbors_within_window}, "
        f"max_edge_days={recommended.max_edge_days}, "
        f"edge_balance_days={recommended.edge_balance_days})"
    )
    print(f"Alternative 'earliest' date: {earliest.isoformat()}")
    print(f"Alternative 'center' date: {center.isoformat()}")
    print()

    top_k = max(1, args.top_k)
    print(f"Top {min(top_k, len(ranked))} candidates:")
    print("rank  date        neighbors  max_edge_days  edge_balance_days")
    print("----  ----------  ---------  -------------  -----------------")
    for i, cand in enumerate(ranked[:top_k], start=1):
        print(
            f"{i:>4}  {cand.value.isoformat():<10}  "
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
        "recommended_reference_date": recommended.value.isoformat(),
        "earliest_reference_date": earliest.isoformat(),
        "center_reference_date": center.isoformat(),
        "ranking": [
            {
                "date": c.value.isoformat(),
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
