"""Regression tests for new QC visualization helpers."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path


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
        scripts_dir = repo_root / "miami/scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        cls.ifg_qc = _load_module(repo_root / "miami/scripts/plot_ifg_network_qc.py")

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


if __name__ == "__main__":
    unittest.main()
