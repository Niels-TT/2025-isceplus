"""Regression tests for new QC visualization helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


class QCVisualizationTests(unittest.TestCase):
    """Covers interferogram-network helper behaviors."""

    @classmethod
    def setUpClass(cls) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        scripts_dir = repo_root / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        cls.ifg_qc = _load_module(repo_root / "scripts/10_plot_ifg_network_qc.py")

    def test_extract_date_from_name(self) -> None:
        parse = self.ifg_qc.extract_date_from_name
        self.assertEqual(parse("t048_101101_iw3_20150921.h5"), date(2015, 9, 21))
        self.assertEqual(parse("/tmp/foo_20220430_extra.tif"), date(2022, 4, 30))
        self.assertIsNone(parse("no_date_here.txt"))

    def test_build_ifg_edges_bandwidth_only(self) -> None:
        dates = [
            date(2020, 1, 1),
            date(2020, 1, 13),
            date(2020, 1, 25),
            date(2020, 2, 6),
            date(2020, 2, 18),
        ]
        edges = self.ifg_qc.build_ifg_edges(
            dates=dates,
            max_bandwidth=2,
            max_temporal_baseline_days=None,
        )
        # N=5 with bandwidth=2 -> 4 (lag1) + 3 (lag2) edges.
        self.assertEqual(len(edges), 7)

    def test_build_ifg_edges_with_temporal_cap(self) -> None:
        dates = [
            date(2020, 1, 1),
            date(2020, 1, 13),
            date(2020, 1, 25),
            date(2020, 2, 6),
        ]
        edges = self.ifg_qc.build_ifg_edges(
            dates=dates,
            max_bandwidth=3,
            max_temporal_baseline_days=20,
        )
        # Keep only pairs <= 20 days apart.
        self.assertTrue(all(edge.days <= 20 for edge in edges))
        self.assertEqual(len(edges), 3)

    def test_graph_metrics_connected(self) -> None:
        edges = [
            self.ifg_qc.Edge(i=0, j=1, days=12),
            self.ifg_qc.Edge(i=1, j=2, days=12),
            self.ifg_qc.Edge(i=2, j=3, days=12),
        ]
        metrics = self.ifg_qc.graph_metrics(n_nodes=4, edges=edges)
        self.assertEqual(metrics["connected_components"], 1)
        self.assertTrue(metrics["is_connected"])

    def test_load_date_baselines_from_reference_suggestions(self) -> None:
        payload = {
            "ranking": [
                {"date": "2025-01-01", "mean_perpendicular_baseline_m": 10.0},
                {"date": "2025-01-13", "mean_perpendicular_baseline_m": -5.0},
                {"date": "2025-01-25", "mean_perpendicular_baseline_m": None},
            ]
        }
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "reference_date_suggestions.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            out = self.ifg_qc.load_date_baselines_from_reference_suggestions(path)

        self.assertAlmostEqual(out[date(2025, 1, 1)], -10.0)
        self.assertAlmostEqual(out[date(2025, 1, 13)], 5.0)
        self.assertNotIn(date(2025, 1, 25), out)

    def test_build_perp_baseline_series_interpolates_and_centers(self) -> None:
        dates = [date(2025, 1, 1), date(2025, 1, 13), date(2025, 1, 25)]
        series, source, known = self.ifg_qc.build_perp_baseline_series(
            dates=dates,
            baseline_by_date={
                date(2025, 1, 1): 0.0,
                date(2025, 1, 25): 20.0,
            },
        )
        self.assertEqual(known, 2)
        self.assertIn("interpolated", source)
        self.assertEqual(len(series), 3)
        self.assertAlmostEqual(series[0], -10.0, places=4)
        self.assertAlmostEqual(series[1], 0.0, places=4)
        self.assertAlmostEqual(series[2], 10.0, places=4)


if __name__ == "__main__":
    unittest.main()
