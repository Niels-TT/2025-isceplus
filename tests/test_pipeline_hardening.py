"""Minimal regression tests for pipeline hardening changes."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PipelineHardeningTests(unittest.TestCase):
    """Covers resume fingerprinting and strict grid checks."""

    @classmethod
    def setUpClass(cls) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        scripts_dir = repo_root / "miami/scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        cls.run_compass = _load_module(repo_root / "miami/scripts/run_compass_runfiles.py")
        cls.prepare_dolphin = _load_module(repo_root / "miami/scripts/prepare_dolphin_workflow.py")
        cls.export_points = _load_module(repo_root / "miami/scripts/export_dolphin_points.py")
        cls.stack_common = _load_module(repo_root / "miami/scripts/stack_common.py")

    def test_runfile_sha256_changes_when_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runfile = Path(td) / "run_test.sh"
            runfile.write_text("echo first\n", encoding="utf-8")
            first = self.run_compass.runfile_sha256(runfile)
            runfile.write_text("echo second\n", encoding="utf-8")
            second = self.run_compass.runfile_sha256(runfile)
        self.assertNotEqual(first, second)

    def test_canonical_option_key_aliases(self) -> None:
        key = self.prepare_dolphin.canonical_option_key("--no-use-evd")
        self.assertEqual(key, "phase-linking.use-evd")
        key2 = self.prepare_dolphin.canonical_option_key("--sx")
        self.assertEqual(key2, "output-options.strides.x")

    def test_select_points_strict_grid_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            velocity = td_path / "velocity.tif"
            coherence = td_path / "coh.tif"
            ps_mask = td_path / "ps.tif"

            profile = {
                "driver": "GTiff",
                "dtype": "float32",
                "count": 1,
                "width": 2,
                "height": 2,
                "crs": "EPSG:4326",
            }

            with rasterio.open(
                velocity,
                "w",
                transform=from_origin(-80.0, 26.0, 0.001, 0.001),
                **profile,
            ) as ds:
                ds.write(np.ones((1, 2, 2), dtype=np.float32))

            # Different transform -> grid mismatch
            with rasterio.open(
                coherence,
                "w",
                transform=from_origin(-80.0, 26.0, 0.002, 0.002),
                **profile,
            ) as ds:
                ds.write(np.ones((1, 2, 2), dtype=np.float32))

            with rasterio.open(
                ps_mask,
                "w",
                transform=from_origin(-80.0, 26.0, 0.002, 0.002),
                **profile,
            ) as ds:
                ds.write(np.ones((1, 2, 2), dtype=np.float32))

            with self.assertRaises(RuntimeError):
                self.export_points.select_points(
                    velocity_file=velocity,
                    coherence_file=coherence,
                    ps_mask_file=ps_mask,
                    min_temporal_coherence=0.0,
                    use_ps_mask=True,
                    stride=1,
                    max_points=0,
                    strict_grid_match=True,
                )

    def test_resolve_stack_config_single_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = root / "projects" / "my_city" / "insar" / "stack_a" / "config" / "processing_configuration.toml"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text("[project]\nname='x'\n", encoding="utf-8")

            resolved = self.stack_common.resolve_stack_config(root, "")
            self.assertEqual(resolved, cfg.resolve())

    def test_resolve_stack_config_multiple_candidates_requires_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg1 = root / "projects" / "a" / "insar" / "stack_a" / "config" / "processing_configuration.toml"
            cfg2 = root / "miami" / "insar" / "stack_b" / "config" / "processing_configuration.toml"
            cfg1.parent.mkdir(parents=True, exist_ok=True)
            cfg2.parent.mkdir(parents=True, exist_ok=True)
            cfg1.write_text("[project]\nname='a'\n", encoding="utf-8")
            cfg2.write_text("[project]\nname='b'\n", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                self.stack_common.resolve_stack_config(root, "")


if __name__ == "__main__":
    unittest.main()
