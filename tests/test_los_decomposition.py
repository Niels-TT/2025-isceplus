"""Regression tests for LOS decomposition workflow."""

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
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


class LOSDecompositionTests(unittest.TestCase):
    """Covers decomposition matrix checks and raster outputs."""

    @classmethod
    def setUpClass(cls) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cls.decomp = _load_module(repo_root / "scripts/decompose_los_velocity.py")

    def _write_raster(self, path: Path, arr: np.ndarray) -> None:
        profile = {
            "driver": "GTiff",
            "height": arr.shape[0],
            "width": arr.shape[1],
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:4326",
            "transform": from_origin(-80.2, 25.95, 0.001, 0.001),
        }
        with rasterio.open(path, "w", **profile) as ds:
            ds.write(arr.astype(np.float32), 1)

    def test_matrix_diagnostics_invertible(self) -> None:
        inv, det, cond = self.decomp.matrix_diagnostics(-0.62, 0.78, 0.62, 0.78)
        self.assertNotEqual(det, 0.0)
        self.assertTrue(np.isfinite(cond))
        self.assertEqual(inv.shape, (2, 2))

    def test_run_decomposition_writes_expected_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            asc_vel = root / "asc_velocity.tif"
            dsc_vel = root / "dsc_velocity.tif"
            asc_coh = root / "asc_coh.tif"
            dsc_coh = root / "dsc_coh.tif"

            asc_e, asc_u = -0.62, 0.78
            dsc_e, dsc_u = 0.62, 0.78

            east_true = np.array(
                [
                    [0.001, 0.002, 0.003],
                    [0.000, -0.001, -0.002],
                    [0.004, 0.003, 0.002],
                ],
                dtype=np.float32,
            )
            up_true = np.array(
                [
                    [0.005, 0.004, 0.003],
                    [0.002, 0.001, 0.000],
                    [-0.001, -0.002, -0.003],
                ],
                dtype=np.float32,
            )

            asc_los = asc_e * east_true + asc_u * up_true
            dsc_los = dsc_e * east_true + dsc_u * up_true
            coh = np.ones_like(east_true, dtype=np.float32)
            coh[0, 0] = 0.2

            self._write_raster(asc_vel, asc_los)
            self._write_raster(dsc_vel, dsc_los)
            self._write_raster(asc_coh, coh)
            self._write_raster(dsc_coh, coh)

            config = root / "config.toml"
            config.write_text(
                "\n".join(
                    [
                        "[processing.decomposition]",
                        "enabled = true",
                        "output_dir = 'decomp'",
                        "target_grid = 'asc'",
                        "min_temporal_coherence = 0.5",
                        "max_condition_number = 100.0",
                        "write_consistency_error = true",
                        "velocity_resampling = 'bilinear'",
                        "coherence_resampling = 'bilinear'",
                        "",
                        "[processing.decomposition.track_asc]",
                        "name = 'asc'",
                        "velocity_file = 'asc_velocity.tif'",
                        "coherence_file = 'asc_coh.tif'",
                        f"los_east_coeff = {asc_e}",
                        f"los_up_coeff = {asc_u}",
                        "",
                        "[processing.decomposition.track_dsc]",
                        "name = 'dsc'",
                        "velocity_file = 'dsc_velocity.tif'",
                        "coherence_file = 'dsc_coh.tif'",
                        f"los_east_coeff = {dsc_e}",
                        f"los_up_coeff = {dsc_u}",
                    ]
                ),
                encoding="utf-8",
            )

            cfg = self.decomp.parse_config(repo_root=root, config_path=config)
            rc = self.decomp.run_decomposition(cfg=cfg, config_path=config, dry_run=False)
            self.assertEqual(rc, 0)

            out_dir = root / "decomp"
            east_out = out_dir / "east_velocity_m_per_year.tif"
            up_out = out_dir / "up_velocity_m_per_year.tif"
            valid_out = out_dir / "valid_mask.tif"
            summary_out = out_dir / "decomposition_summary.json"

            self.assertTrue(east_out.exists())
            self.assertTrue(up_out.exists())
            self.assertTrue(valid_out.exists())
            self.assertTrue(summary_out.exists())

            with rasterio.open(east_out) as ds:
                east_est = ds.read(1)
            with rasterio.open(up_out) as ds:
                up_est = ds.read(1)
            with rasterio.open(valid_out) as ds:
                valid = ds.read(1)

            self.assertEqual(int(valid[0, 0]), 0)
            np.testing.assert_allclose(east_est[1:, 1:], east_true[1:, 1:], rtol=1e-5, atol=1e-7)
            np.testing.assert_allclose(up_est[1:, 1:], up_true[1:, 1:], rtol=1e-5, atol=1e-7)


if __name__ == "__main__":
    unittest.main()
