#!/usr/bin/env python3
"""Generate QC visualization for Dolphin interferogram network.

Technical summary:
    Reads validated CSLC file list and Dolphin network settings from project
    config, builds the expected interferogram graph, and exports:
    1) network PNG for quick visual QA
    2) summary JSON with key graph metrics

Why:
    Interferogram-network structure strongly affects inversion quality, so this
    provides a fast checkpoint before full time-series processing.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import networkx as nx

from stack_common import (
    DEFAULT_STACK_CONFIG_REL,
    infer_stack_root,
    read_toml,
    resolve_path,
    resolve_stack_config,
)

DATE_RE = re.compile(r"(20\d{6})")


@dataclass(frozen=True)
class Edge:
    """Interferogram edge between two acquisition indices."""

    i: int
    j: int
    days: int


def extract_date_from_name(path_text: str) -> date | None:
    """Extract acquisition date from CSLC file path/name.

    Args:
        path_text: Full path or filename containing an 8-digit date.

    Returns:
        Parsed date or None when no valid date token exists.
    """
    m = DATE_RE.search(path_text)
    if not m:
        return None
    token = m.group(1)
    try:
        return datetime.strptime(token, "%Y%m%d").date()
    except ValueError:
        return None


def read_cslc_dates(cslc_file_list: Path) -> list[date]:
    """Read sorted unique acquisition dates from CSLC list file."""
    if not cslc_file_list.exists():
        raise FileNotFoundError(f"Missing CSLC list file: {cslc_file_list}")

    dates: set[date] = set()
    for raw in cslc_file_list.read_text(encoding="utf-8").splitlines():
        text = raw.strip()
        if not text:
            continue
        parsed = extract_date_from_name(Path(text).name)
        if parsed is None:
            parsed = extract_date_from_name(text)
        if parsed is not None:
            dates.add(parsed)

    out = sorted(dates)
    if len(out) < 2:
        raise RuntimeError(
            f"Need at least 2 dated CSLC entries for network plot, found {len(out)}."
        )
    return out


def build_ifg_edges(
    dates: list[date],
    max_bandwidth: int,
    max_temporal_baseline_days: int | None,
) -> list[Edge]:
    """Build nearest-neighbor interferogram edges.

    Args:
        dates: Sorted acquisition dates.
        max_bandwidth: Max neighbor distance in index space.
        max_temporal_baseline_days: Optional max temporal baseline constraint.

    Returns:
        List of edge records.
    """
    if max_bandwidth < 1:
        raise ValueError("max_bandwidth must be >= 1.")

    n = len(dates)
    edges: list[Edge] = []
    for i in range(n):
        upper = min(n, i + max_bandwidth + 1)
        for j in range(i + 1, upper):
            dt = (dates[j] - dates[i]).days
            if max_temporal_baseline_days is not None and dt > max_temporal_baseline_days:
                continue
            edges.append(Edge(i=i, j=j, days=dt))
    return edges


def graph_metrics(n_nodes: int, edges: list[Edge]) -> dict[str, Any]:
    """Compute graph-level QC metrics."""
    g = nx.Graph()
    g.add_nodes_from(range(n_nodes))
    g.add_edges_from((e.i, e.j) for e in edges)

    components = nx.number_connected_components(g)
    degrees = [deg for _, deg in g.degree()]
    mean_degree = float(sum(degrees) / n_nodes) if n_nodes else 0.0
    min_degree = min(degrees) if degrees else 0
    max_degree = max(degrees) if degrees else 0

    return {
        "connected_components": components,
        "is_connected": components == 1,
        "mean_degree": round(mean_degree, 3),
        "min_degree": min_degree,
        "max_degree": max_degree,
        "edge_count": len(edges),
        "node_count": n_nodes,
    }


def plot_network_png(
    *,
    dates: list[date],
    edges: list[Edge],
    out_png: Path,
    dpi: int,
    title: str,
    subtitle: str,
) -> None:
    """Render interferogram network as arc graph on time axis."""
    out_png.parent.mkdir(parents=True, exist_ok=True)

    datetimes = [datetime.combine(d, datetime.min.time()) for d in dates]
    x = mdates.date2num(datetimes)

    fig, ax = plt.subplots(figsize=(13.0, 5.6), dpi=110)
    ax.set_facecolor("#fcfcfd")

    # Draw edges as simple arcs above timeline.
    for edge in edges:
        x0 = x[edge.i]
        x1 = x[edge.j]
        xm = (x0 + x1) * 0.5
        gap = max(1, edge.j - edge.i)
        amp = 0.15 + 0.17 * math.sqrt(gap)
        ax.plot(
            [x0, xm, x1],
            [0.0, amp, 0.0],
            color="#4379b3",
            linewidth=1.0,
            alpha=0.35,
            zorder=2,
        )

    ax.scatter(x, [0.0] * len(x), s=34, color="#1a1a1a", zorder=5)

    # Date labels are sampled to keep readability on dense stacks.
    max_labels = 14
    step = max(1, math.ceil(len(x) / max_labels))
    shown_idx = set(range(0, len(x), step))
    shown_idx.add(len(x) - 1)

    for i, d in enumerate(dates):
        if i not in shown_idx:
            continue
        ax.text(
            x[i],
            -0.06,
            d.strftime("%Y-%m-%d"),
            rotation=45,
            ha="right",
            va="top",
            fontsize=8.2,
            color="#333333",
        )

    ax.set_ylim(-0.15, None)
    ax.set_yticks([])
    ax.set_xlabel("Acquisition date")
    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.grid(axis="x", linestyle="--", color="#d8dde5", alpha=0.7)

    ax.set_title(title, fontsize=13, fontweight="bold", pad=10)
    ax.text(
        0.01,
        0.96,
        subtitle,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9.4,
        color="#444444",
    )

    fig.savefig(out_png, dpi=max(100, dpi), bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


def int_or_none(value: Any) -> int | None:
    """Convert optional numeric config value to positive int or None."""
    if value is None:
        return None
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return None
    if iv <= 0:
        return None
    return iv


def main() -> int:
    """Parse CLI args and write interferogram network QC artifacts."""
    parser = argparse.ArgumentParser(
        description="Create Dolphin interferogram-network QC figure from prepared CSLC list."
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
        "--output-png",
        default="",
        help="Override output PNG path.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Override output summary JSON path.",
    )
    parser.add_argument(
        "--max-bandwidth",
        type=int,
        default=0,
        help="Override interferogram max bandwidth (0 = config value).",
    )
    parser.add_argument(
        "--max-temporal-baseline-days",
        type=int,
        default=0,
        help="Override max temporal baseline in days (0 = config value).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=260,
        help="Output PNG resolution in DPI.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved paths/settings without writing outputs.",
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

    project_name = str(cfg.get("project", {}).get("name", "stack"))
    dolphin_cfg = cfg.get("processing", {}).get("dolphin", {})
    qc_cfg = dolphin_cfg.get("qc", {})

    work_dir = resolve_path(
        repo_root,
        str(
            dolphin_cfg.get(
                "work_dir",
                stack_root / "stack" / "dolphin",
            )
        ),
    )
    cslc_file_list = resolve_path(
        repo_root,
        str(
            dolphin_cfg.get(
                "cslc_file_list",
                stack_root / "stack" / "dolphin" / "inputs" / "cslc_files.txt",
            )
        ),
    )

    default_png = resolve_path(
        repo_root,
        str(
            qc_cfg.get(
                "ifg_network_png",
                work_dir / "qc" / "ifg_network.png",
            )
        ),
    )
    output_png = resolve_path(repo_root, args.output_png) if args.output_png else default_png

    default_json = resolve_path(
        repo_root,
        str(
            qc_cfg.get(
                "ifg_network_summary_json",
                work_dir / "qc" / "ifg_network_summary.json",
            )
        ),
    )
    output_json = resolve_path(repo_root, args.output_json) if args.output_json else default_json

    configured_bw = int_or_none(qc_cfg.get("max_bandwidth_override"))
    if configured_bw is None:
        configured_bw = int_or_none(dolphin_cfg.get("max_bandwidth"))
    max_bandwidth = args.max_bandwidth if args.max_bandwidth > 0 else (configured_bw or 3)

    configured_temp = int_or_none(qc_cfg.get("max_temporal_baseline_days_override"))
    if configured_temp is None:
        configured_temp = int_or_none(dolphin_cfg.get("max_temporal_baseline"))
    max_temporal_baseline = (
        args.max_temporal_baseline_days
        if args.max_temporal_baseline_days > 0
        else (configured_temp or None)
    )

    dates = read_cslc_dates(cslc_file_list)
    edges = build_ifg_edges(
        dates=dates,
        max_bandwidth=max_bandwidth,
        max_temporal_baseline_days=max_temporal_baseline,
    )
    metrics = graph_metrics(len(dates), edges)

    print(f"Config: {config_path}")
    print(f"CSLC list: {cslc_file_list}")
    print(f"Unique acquisition dates: {len(dates)}")
    print(f"Max bandwidth: {max_bandwidth}")
    print(
        "Max temporal baseline (days): "
        f"{max_temporal_baseline if max_temporal_baseline is not None else 'none'}"
    )
    print(f"Output PNG: {output_png}")
    print(f"Output JSON: {output_json}")
    print(f"Edge count: {metrics['edge_count']}")
    print(f"Connected components: {metrics['connected_components']}")

    if args.dry_run:
        return 0

    title = f"Dolphin Interferogram Network ({project_name})"
    subtitle = (
        f"{len(dates)} dates | {metrics['edge_count']} edges | "
        f"max_bandwidth={max_bandwidth}"
    )
    if max_temporal_baseline is not None:
        subtitle += f" | max_temporal_baseline_days={max_temporal_baseline}"

    plot_network_png(
        dates=dates,
        edges=edges,
        out_png=output_png,
        dpi=max(100, args.dpi),
        title=title,
        subtitle=subtitle,
    )

    payload = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "cslc_file_list": str(cslc_file_list),
        "output_png": str(output_png),
        "output_json": str(output_json),
        "max_bandwidth": max_bandwidth,
        "max_temporal_baseline_days": max_temporal_baseline,
        "first_date": dates[0].isoformat(),
        "last_date": dates[-1].isoformat(),
        "metrics": metrics,
        "edges": [
            {
                "i": e.i,
                "j": e.j,
                "ref_date": dates[e.i].isoformat(),
                "sec_date": dates[e.j].isoformat(),
                "days": e.days,
            }
            for e in edges
        ],
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("Interferogram network QC generated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
