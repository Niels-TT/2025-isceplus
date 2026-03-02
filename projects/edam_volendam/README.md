# edam_volendam Workflow

This runbook is for the already-created Edam/Volendam dual-track project.
Run all commands from repo root.

```bash
cd /home/niels/insar/git/2025-isceplus
```

## 0) AOI Sanity Check
Replace `projects/edam_volendam/aux/bbox.kml` with your Edam/Volendam polygon.
Use Google Earth Pro export as KML (WGS84 lon/lat, EPSG:4326).
Why: AOI is the source of truth for final footprint and cropping.

Set this in both stack configs (ASC + DSC):
- `[aoi].buffer_m = 3000.0` (default): processing/search buffer in meters.

Buffer policy:
- `aoi.kml` = project footprint you want to analyze.
- `aoi.buffer_m` = expanded footprint for discovery/search/DEM/COMPASS.
- Dolphin output is cropped back to project AOI when `processing.dolphin.crop_to_project_aoi = true` (default).

If this file still contains the template Miami polygon, discovery/search will show Florida-like results (for example ASC orbit 48).

## 1) Environment and Credentials
Why: most first-run failures are missing dependencies or credentials.
```bash
mamba env update -n isce3-feb -f envs/isce3-feb.yml
conda activate isce3-feb
python scripts/00_patch_rasterio_float16.py
bash scripts/00_check_credentials.sh
```

## 2) Optional Geometry Discovery (Orbit/Frame Selection)
Run once for the project (single AOI/date window). Discovery already scans all
flight directions/orbits/frames, so ASC+DSC do not need separate discovery runs
unless their AOI or date windows differ.
Why: choose direction/orbit/frame from measured coverage instead of guessing.

```bash
mamba run -n isce3-feb python scripts/02_discover_s1_candidates.py \
  --repo-root . \
  --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml \
  --min-unique-dates 1
```

Discovery uses the buffered AOI from `aoi.buffer_m`.

Outputs:
- `search/candidates/geometry_candidates.csv`
- `search/candidates/geometry_candidates.json`
- `search/candidates/stack_candidates.png`

Pick one ASCENDING candidate and one DESCENDING candidate from that table,
then set both `relative_orbit` and integer `frame_number` in each config
(`frame_number = 0` disables frame filtering).

## 3) First-Pass Config Rules (Before First Search)
In both configs:
- keep `[aoi].buffer_m` equal for ASC and DSC when you want consistent decomposition coverage
- keep `expected_scenes = 0` and `expected_unique_dates = 0`
- keep `selection.require_reference = false` for first search
- keep `selection.max_dates` at desired stack size

Why: `04_suggest_reference_date.py` needs `search/products/scenes.csv`, and that file is only created by `03_search_s1_stack.py`.

## 4) Mandatory Stack Search (Creates `scenes.csv`)
Run once per stack:
Why: this materializes deterministic scene manifests consumed by all later steps.

```bash
mamba run -n isce3-feb python scripts/03_search_s1_stack.py \
  --repo-root . \
  --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml

mamba run -n isce3-feb python scripts/03_search_s1_stack.py \
  --repo-root . \
  --config projects/edam_volendam/insar/edam_volendam_s1_dsc_t000/config/processing_configuration.toml
```

Check that these now exist:
- `projects/edam_volendam/insar/edam_volendam_s1_asc_t000/search/products/scenes.csv`
- `projects/edam_volendam/insar/edam_volendam_s1_dsc_t000/search/products/scenes.csv`

## 5) Choose Reference Dates
Why: a stable reference improves temporal inversion conditioning and baseline behavior.

Run once per stack:
```bash
mamba run -n isce3-feb python scripts/04_suggest_reference_date.py \
  --repo-root . \
  --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml

mamba run -n isce3-feb python scripts/04_suggest_reference_date.py \
  --repo-root . \
  --config projects/edam_volendam/insar/edam_volendam_s1_dsc_t000/config/processing_configuration.toml
```

Update each config with your chosen `reference_date`.
Then optionally tighten rules:
- set `selection.require_reference = true`
- set `expected_scenes` and `expected_unique_dates` from search summary

Re-run search (`03_search_s1_stack.py`) after tightening to verify deterministic counts.

## 6) Main Pipeline Per Stack
Run ASC stack first, then DSC stack:
Why: each stage consumes outputs from the previous stage for that stack.

```bash
# ASC: Download plan (dry-run)
mamba run -n isce3-feb python scripts/05_download_s1_stack.py --repo-root . --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml

# ASC: Real download
mamba run -n isce3-feb python scripts/05_download_s1_stack.py --repo-root . --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml --download

# ASC: DEM
mamba run -n isce3-feb python scripts/06_download_dem_opentopography.py --repo-root . --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml

# ASC: COMPASS prepare/run
mamba run -n isce3-feb python scripts/07_prepare_compass_stack.py --repo-root . --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml
mamba run -n isce3-feb python scripts/08_run_compass_runfiles.py --repo-root . --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml

# ASC: Dolphin prepare/run
mamba run -n isce3-feb python scripts/09_prepare_dolphin_workflow.py --repo-root . --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml
mamba run -n isce3-feb python scripts/11_run_dolphin_workflow.py --repo-root . --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml
```

Then repeat the same six commands with:
`projects/edam_volendam/insar/edam_volendam_s1_dsc_t000/config/processing_configuration.toml`

Note:
- `07_prepare_compass_stack.py` auto-downloads the OPERA burst DB to `processing.compass.burst_db_file` when missing.
- You no longer need a manual `curl` step before 07.

## 7) Decomposition (After Both Dolphin Runs)
```bash
mamba run -n isce3-feb python scripts/90_decompose_los_velocity.py \
  --repo-root . \
  --config projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml
```

You can also pass:
`projects/edam_volendam/insar/edam_volendam_s1_dsc_t000/config/processing_configuration.toml`
Decomposition settings in both files are cross-wired to ASC+DSC velocity outputs.
Naming note: `90_` is used for optional end-of-pipeline utilities, separate from core run-order stages.
If `[processing.decomposition.point_exports]` and/or `[processing.decomposition.raster_viz]`
are enabled, `90_decompose_los_velocity.py` also writes decomposition CSV/KMZ points and
decomposition raster quicklook KMZ/GeoTIFF products automatically.

## Troubleshooting
- `04_suggest_reference_date.py` says `Missing scenes CSV`:
  run step 4 (`03_search_s1_stack.py`) first.
- Discovery shows only one candidate group:
  run discovery with `--min-unique-dates 1` to see all groups; default filtering can hide sparse groups.
- Search returns `Selected scenes: 0`:
  AOI KML and/or `relative_orbit` is mismatched.
  Re-check `projects/edam_volendam/aux/bbox.kml`, then re-run discovery and update orbit.
- `07_prepare_compass_stack.py` fails with scene completeness errors:
  finish/retry step 5 download (`05_download_s1_stack.py --download`) for that stack, then rerun 07.
- `08_run_compass_runfiles.py` says `Missing run_files directory`:
  step 07 did not complete successfully for that stack; fix 07 error first, then rerun 08.
