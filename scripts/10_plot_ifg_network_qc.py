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
import matplotlib as mpl
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from mpl_toolkits.axes_grid1 import make_axes_locatable

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


def infer_reference_suggestions_json(
    *,
    repo_root: Path,
    cfg: dict[str, Any],
    stack_root: Path,
) -> Path | None:
    """Resolve reference-date suggestions JSON path, if present."""
    outputs_cfg = cfg.get("outputs", {})
    candidates: list[Path] = []

    root_raw = str(outputs_cfg.get("root", "")).strip()
    metadata_raw = str(outputs_cfg.get("metadata_csv", "")).strip()
    if root_raw:
        root_path = resolve_path(repo_root, root_raw)
        if metadata_raw:
            candidates.append((root_path / metadata_raw).parent / "reference_date_suggestions.json")
        candidates.append(root_path / "products" / "reference_date_suggestions.json")

    candidates.append(stack_root / "search" / "products" / "reference_date_suggestions.json")

    for path in candidates:
        if path.exists():
            return path
    return None


def load_date_baselines_from_reference_suggestions(path: Path) -> dict[date, float]:
    """Load relative per-date baselines from reference-date suggestions output.

    Notes:
        `mean_perpendicular_baseline_m` in ranking is `mean(rel) - rel(date)`.
        Negating it recovers relative baseline up to an additive constant, which
        is sufficient for plotting after centering.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    ranking = payload.get("ranking")
    if not isinstance(ranking, list):
        return {}

    out: dict[date, float] = {}
    for row in ranking:
        if not isinstance(row, dict):
            continue
        token = str(row.get("date", "")).strip()
        mean_perp = row.get("mean_perpendicular_baseline_m")
        if not token or mean_perp is None:
            continue
        try:
            d = datetime.strptime(token, "%Y-%m-%d").date()
            out[d] = -float(mean_perp)
        except (TypeError, ValueError):
            continue
    return out


def build_perp_baseline_series(
    *,
    dates: list[date],
    baseline_by_date: dict[date, float],
) -> tuple[list[float], str, int]:
    """Build per-date baseline series, with interpolation and zero-mean centering."""
    raw: list[float | None] = [baseline_by_date.get(d) for d in dates]
    known_idx = [i for i, value in enumerate(raw) if value is not None]
    if not known_idx:
        return [0.0] * len(dates), "flat_zero", 0

    values = [float(v) if v is not None else None for v in raw]
    for i, value in enumerate(values):
        if value is not None:
            continue
        left_idx = max((k for k in known_idx if k < i), default=None)
        right_idx = min((k for k in known_idx if k > i), default=None)
        if left_idx is not None and right_idx is not None:
            left_val = float(values[left_idx])  # type: ignore[arg-type]
            right_val = float(values[right_idx])  # type: ignore[arg-type]
            frac = (i - left_idx) / (right_idx - left_idx)
            values[i] = left_val + frac * (right_val - left_val)
        elif left_idx is not None:
            values[i] = float(values[left_idx])  # type: ignore[arg-type]
        elif right_idx is not None:
            values[i] = float(values[right_idx])  # type: ignore[arg-type]
        else:
            values[i] = 0.0

    series = [float(v) for v in values]
    mean_value = float(sum(series) / len(series)) if series else 0.0
    centered = [v - mean_value for v in series]
    source = "reference_date_suggestions_json"
    if len(known_idx) < len(dates):
        source += "+interpolated"
    return centered, source, len(known_idx)


def read_edge_coherence(
    *,
    interferograms_dir: Path,
    dates: list[date],
    edges: list[Edge],
) -> dict[tuple[int, int], float]:
    """Read average spatial coherence for each edge from Dolphin .int.cor.tif files."""
    try:
        import rasterio
    except ModuleNotFoundError:
        return {}

    if not interferograms_dir.exists():
        return {}

    out: dict[tuple[int, int], float] = {}
    for edge in edges:
        name = f"{dates[edge.i].strftime('%Y%m%d')}_{dates[edge.j].strftime('%Y%m%d')}.int.cor.tif"
        path = interferograms_dir / name
        if not path.exists():
            continue
        try:
            with rasterio.open(path) as ds:
                arr = ds.read(1, masked=True)
        except Exception:
            continue

        values = np.ma.masked_invalid(arr).compressed()
        if values.size == 0:
            continue
        mean_value = float(np.mean(values))
        if math.isfinite(mean_value):
            out[(edge.i, edge.j)] = float(min(1.0, max(0.0, mean_value)))
    return out


def plot_network_png(
    *,
    dates: list[date],
    baselines_m: list[float],
    edges: list[Edge],
    edge_coherence: dict[tuple[int, int], float],
    out_png: Path,
    dpi: int,
    title: str,
    subtitle: str,
) -> None:
    """Render interferogram network in time-perpendicular-baseline coordinates."""
    out_png.parent.mkdir(parents=True, exist_ok=True)

    datetimes = [datetime.combine(d, datetime.min.time()) for d in dates]
    fig, ax = plt.subplots(figsize=(10.8, 5.8), dpi=110)

    coh_available = bool(edge_coherence)
    cmap = plt.get_cmap("RdBu")
    norm = mpl.colors.Normalize(vmin=0.2, vmax=1.0)

    edge_order = (
        sorted(edges, key=lambda e: edge_coherence.get((e.i, e.j), -1.0))
        if coh_available
        else edges
    )
    for edge in edge_order:
        x = [datetimes[edge.i], datetimes[edge.j]]
        y = [baselines_m[edge.i], baselines_m[edge.j]]
        coh = edge_coherence.get((edge.i, edge.j))
        if coh is not None:
            color = cmap(norm(coh))
            width = 2.0
        else:
            color = "#9ebfd9"
            width = 1.3
        ax.plot(x, y, "-", lw=width, alpha=0.72, c=color, zorder=1)

    ax.plot(
        datetimes,
        baselines_m,
        "o",
        ms=10,
        alpha=0.9,
        mfc="#f2b134",
        mec="#4a4a4a",
        mew=1.1,
        linestyle="None",
        zorder=3,
    )

    date_locator = mdates.AutoDateLocator(minticks=5, maxticks=9)
    ax.xaxis.set_major_locator(date_locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%Y"))
    ax.tick_params(axis="x", labelrotation=35)
    for label in ax.get_xticklabels():
        label.set_ha("right")

    ax.set_xlabel("Acquisition date")
    ax.set_ylabel("Perp Baseline [m]")
    ax.tick_params(which="both", direction="in", bottom=True, top=True, left=True, right=True)

    ax.set_title(title, fontsize=13, pad=6)
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

    if coh_available:
        cax = make_axes_locatable(ax).append_axes("right", "3%", pad="3%")
        sm = mpl.cm.ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        cbar = fig.colorbar(sm, cax=cax)
        cbar.set_label("Average Spatial Coherence")

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

    reference_suggestions_json = infer_reference_suggestions_json(
        repo_root=repo_root,
        cfg=cfg,
        stack_root=stack_root,
    )
    baseline_by_date: dict[date, float] = {}
    if reference_suggestions_json is not None:
        baseline_by_date = load_date_baselines_from_reference_suggestions(
            reference_suggestions_json
        )
    baselines_m, baseline_source, baseline_available_count = build_perp_baseline_series(
        dates=dates,
        baseline_by_date=baseline_by_date,
    )

    interferograms_dir = work_dir / "interferograms"
    edge_coherence = read_edge_coherence(
        interferograms_dir=interferograms_dir,
        dates=dates,
        edges=edges,
    )

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
    print(f"Baseline source: {baseline_source}")
    print(f"Baseline values available: {baseline_available_count}/{len(dates)}")
    print(f"Interferogram coherence edges available: {len(edge_coherence)}/{len(edges)}")

    if args.dry_run:
        return 0

    title = "Interferogram Network"
    subtitle_parts = [
        f"{project_name}",
        f"{len(dates)} dates",
        f"{metrics['edge_count']} edges",
        f"max_bandwidth={max_bandwidth}",
    ]
    if max_temporal_baseline is not None:
        subtitle_parts.append(f"max_temporal_baseline_days={max_temporal_baseline}")
    if edge_coherence:
        subtitle_parts.append(f"coherence_edges={len(edge_coherence)}")
    subtitle = " | ".join(subtitle_parts)

    plot_network_png(
        dates=dates,
        baselines_m=baselines_m,
        edges=edges,
        edge_coherence=edge_coherence,
        out_png=output_png,
        dpi=max(100, args.dpi),
        title=title,
        subtitle=subtitle,
    )

    edge_coherence_values = list(edge_coherence.values())
    edge_coherence_mean = (
        float(sum(edge_coherence_values) / len(edge_coherence_values))
        if edge_coherence_values
        else None
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
        "baseline_source": baseline_source,
        "baseline_reference_suggestions_json": (
            str(reference_suggestions_json) if reference_suggestions_json is not None else None
        ),
        "baseline_available_count": baseline_available_count,
        "date_baselines_m": [
            {"date": d.isoformat(), "perp_baseline_m": round(baselines_m[i], 3)}
            for i, d in enumerate(dates)
        ],
        "interferograms_dir": str(interferograms_dir),
        "edge_coherence_count": len(edge_coherence),
        "edge_coherence_mean": round(edge_coherence_mean, 4)
        if edge_coherence_mean is not None
        else None,
        "metrics": metrics,
        "edges": [
            {
                "i": e.i,
                "j": e.j,
                "ref_date": dates[e.i].isoformat(),
                "sec_date": dates[e.j].isoformat(),
                "days": e.days,
                "avg_spatial_coherence": round(edge_coherence[(e.i, e.j)], 4)
                if (e.i, e.j) in edge_coherence
                else None,
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
