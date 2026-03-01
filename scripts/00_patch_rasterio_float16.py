#!/usr/bin/env python3
"""Patch rasterio 1.4.x dtype maps for GDAL Float16 compatibility.

Why:
    Some rasterio builds paired with GDAL >= 3.11 miss dtype code 15
    (GDT_Float16), which raises `KeyError: 15` when opening Float16 rasters.
    This script backports Float16 mappings directly in rasterio's dtypes module.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import re
import sys
from pathlib import Path

from rasterio.env import GDALVersion
import rasterio.dtypes as rio_dtypes

PATCH_MARKER = "# 2025-isceplus Float16 backport patch"
PATCH_SNIPPET = """
# 2025-isceplus Float16 backport patch
if GDALVersion.runtime().at_least("3.11"):
    dtype_fwd[15] = "float16"  # GDT_Float16
    typename_fwd[15] = "Float16"
    dtype_rev = {v: k for k, v in dtype_fwd.items()}
    dtype_rev["uint8"] = 1
    dtype_rev["complex"] = 11
    dtype_rev["complex_int16"] = 8
    dtype_rev["float16"] = 15
    dtype_ranges.setdefault("float16", (-65504.0, 65504.0))
""".lstrip(
    "\n"
)


def _is_float16_mapping_present(module: object) -> bool:
    """Return True when rasterio dtype maps already include Float16."""
    dtype_fwd = getattr(module, "dtype_fwd", {})
    dtype_rev = getattr(module, "dtype_rev", {})
    return dtype_fwd.get(15) == "float16" and dtype_rev.get("float16") == 15


def _patch_dtypes_file(path: Path) -> bool:
    """Append backport snippet if missing.

    Returns:
        True if file was modified.
    """
    text = path.read_text(encoding="utf-8")
    if PATCH_MARKER in text:
        return False

    # Basic sanity check: this script targets rasterio's dtypes module layout.
    required_tokens = ("dtype_fwd", "dtype_rev", "typename_fwd", "dtype_ranges")
    if not all(token in text for token in required_tokens):
        raise RuntimeError(
            f"Unexpected rasterio.dtypes structure in {path}; refusing to patch."
        )

    # Keep a trailing newline stable.
    if not re.search(r"\n\Z", text):
        text += "\n"
    text += "\n" + PATCH_SNIPPET
    path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    """Patch rasterio dtypes for Float16 when required."""
    parser = argparse.ArgumentParser(
        description="Patch rasterio Float16 dtype mapping for GDAL >= 3.11."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report whether patch is needed without modifying files.",
    )
    args = parser.parse_args()

    gdal_runtime = GDALVersion.runtime()
    print(f"GDAL runtime: {gdal_runtime}")
    if not gdal_runtime.at_least("3.11"):
        print("No patch needed (GDAL < 3.11 does not expose GDT_Float16).")
        return 0

    if _is_float16_mapping_present(rio_dtypes):
        print("rasterio Float16 dtype mapping already present; nothing to do.")
        return 0

    dtypes_path_str = inspect.getsourcefile(rio_dtypes)
    if not dtypes_path_str:
        print("Could not resolve rasterio.dtypes source path.", file=sys.stderr)
        return 2
    dtypes_path = Path(dtypes_path_str).resolve()
    print(f"Patching file: {dtypes_path}")

    if args.dry_run:
        print("Patch required (dry-run).")
        return 1

    modified = _patch_dtypes_file(dtypes_path)
    if modified:
        print("Patch snippet written.")
    else:
        print("Patch marker already present; no file changes made.")

    # Reload module to validate mapping in current process.
    importlib.reload(rio_dtypes)
    if not _is_float16_mapping_present(rio_dtypes):
        print("Patch validation failed: Float16 mapping still missing.", file=sys.stderr)
        return 2

    print("Patch validated: rasterio can map GDAL Float16 (code 15).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
