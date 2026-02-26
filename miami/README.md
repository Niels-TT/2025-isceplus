# Miami InSAR Project

This folder contains the practical pipeline for:
`ASF search -> SLC download -> DEM download -> COMPASS coregistration -> Dolphin time series -> optional point export`.

Note:
- The scripts under `miami/scripts/` are config-driven and reusable for non-Miami projects.
- Use `--config <your_project>/.../processing_configuration.toml` to run the same pipeline on another AOI.
- Generic scaffold and setup flow: `example_project/README.md`.

## Before You Run Anything
Why: most failures come from running outside WSL, wrong Python, or missing credentials.

1. Use a WSL2/Linux shell.
2. Open this repo from Linux path (recommended): `/home/niels/insar/git/2025-isceplus`
3. Activate conda environment:

```bash
cd /home/niels/insar/git/2025-isceplus
mamba env update -n isce3-feb -f envs/isce3-feb.yml
conda activate isce3-feb
```

4. Verify interpreter/pip:

```bash
which python
python -m pip -V
```

5. Verify credentials:

```bash
bash scripts/check_credentials.sh
```

Important:
- Do not run scripts with `/bin/python3`; that bypasses the conda env and causes missing modules.
- Use either `python ...` in activated env, or `mamba run -n isce3-feb python ...`.

## Project Structure
- `aux/bbox.kml`: AOI polygon (source of truth)
- `bbox.kml`: symlink to `aux/bbox.kml`
- `insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml`: central project config
- `insar/us_isleofnormandy_s1_asc_t48/search/`: ASF search artifacts
- `insar/us_isleofnormandy_s1_asc_t48/stack/slc/`: downloaded Sentinel-1 ZIP files
- `insar/us_isleofnormandy_s1_asc_t48/stack/dem/`: DEM files
- `insar/us_isleofnormandy_s1_asc_t48/stack/orbits/`: orbit cache (POEORB/RESORB)
- `insar/us_isleofnormandy_s1_asc_t48/stack/compass/`: COMPASS runconfigs/runfiles/CSLC outputs
- `insar/us_isleofnormandy_s1_asc_t48/stack/dolphin/`: Dolphin config + time-series outputs
- `insar/us_isleofnormandy_s1_asc_t48/logs/`: run logs

## Why This CLI Pattern
Why: explicit CLI args make runs reproducible and independent of terminal location.

We always pass:
- `--repo-root .` to resolve relative paths from repo root
- `--config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml` for deterministic settings

## Stack Definition (Current)
- AOI: `miami/aux/bbox.kml`
- Sensor/mode: Sentinel-1 IW SLC
- Direction/track: ascending, relative orbit 48
- Search window: 2015-09-21 to 2022-04-30
- Selection policy: first 20 acquisition dates from reference date (`2015-09-21`)

## Script Roles
- `scripts/discover_s1_candidates.py` (repo-level): discovery ranking + map of AOI vs candidate stack footprints
- `miami/scripts/search_s1_stack.py`: ASF query + scene manifests
- `miami/scripts/download_s1_stack.py`: storage-aware SLC downloader with resumable manifest + tqdm
- `miami/scripts/download_dem_opentopography.py`: DEM download from OpenTopography
- `miami/scripts/prepare_compass_stack.py`: validates inputs and generates COMPASS run files
- `miami/scripts/run_compass_runfiles.py`: executes COMPASS run files with resume state
- `miami/scripts/prepare_dolphin_workflow.py`: validates CSLC outputs and generates Dolphin YAML
- `miami/scripts/plot_ifg_network_qc.py`: generates interferogram-network QC PNG/JSON from prepared CSLC stack
- `miami/scripts/run_dolphin_workflow.py`: runs Dolphin and optional point export
- `miami/scripts/export_dolphin_points.py`: converts velocity raster to operational CSV/KMZ points
- `scripts/decompose_los_velocity.py`: decomposes ASC/DSC LOS velocity rasters into East/Up components

## Run Order
Why: each stage depends on outputs from the previous one.

1. Search stack (if you need to regenerate search results):

```bash
mamba run -n isce3-feb python miami/scripts/search_s1_stack.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

Optional pre-search geometry discovery (recommended when starting new AOIs):

```bash
mamba run -n isce3-feb python scripts/discover_s1_candidates.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

2. SLC download dry-run (size/free-space check):

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

3. SLC download for real:

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml \
  --download
```

4. Download DEM:

```bash
mamba run -n isce3-feb python miami/scripts/download_dem_opentopography.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

5. Prepare COMPASS:

```bash
mamba run -n isce3-feb python miami/scripts/prepare_compass_stack.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

6. Run COMPASS coregistration:

```bash
mamba run -n isce3-feb python miami/scripts/run_compass_runfiles.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

7. Prepare Dolphin:

```bash
mamba run -n isce3-feb python miami/scripts/prepare_dolphin_workflow.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

8. Run Dolphin:

```bash
mamba run -n isce3-feb python miami/scripts/run_dolphin_workflow.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

Optional debug:

```bash
mamba run -n isce3-feb python miami/scripts/run_dolphin_workflow.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml \
  --debug
```

9. Optional dual-track decomposition (after both ASC + DSC Dolphin runs exist):

```bash
mamba run -n isce3-feb python scripts/decompose_los_velocity.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

Why: this solves the standard 2-geometry linear system per pixel to estimate East/Up velocity from LOS velocities.

## Inputs And Outputs By Stage
Why: clear I/O boundaries make debugging and reruns easier.

- Search
  - Input: AOI KML + `[search]` config
  - Output: `search/products/scenes.csv`, `scene_names.txt`, `summary.json`, `search/raw/results.geojson`, `aoi.wkt`
- Download SLC
  - Input: `scenes.csv` + Earthdata credentials
  - Output: `stack/slc/*.zip`, `stack/download_manifest.json`
- Download DEM
  - Input: AOI + DEM settings + OpenTopography API key
  - Output: `stack/dem/*.tif` + `*.meta.json`
- Prepare COMPASS
  - Input: local SLC ZIPs + DEM + config
  - Output: `stack/compass/runconfigs/*.yaml`, `run_files/*.sh`, `prepare_summary.json`
- Run COMPASS
  - Input: generated run files
  - Output: per-date/per-burst CSLC HDF5 under `stack/compass/`, plus logs and `run_state.json`
- Prepare Dolphin
  - Input: COMPASS CSLC HDF5
  - Output: `stack/dolphin/config/dolphin_config.yaml`, `stack/dolphin/inputs/cslc_files.txt`, `prepare_summary.json`
  - If enabled: `stack/dolphin/qc/ifg_network.png` + `ifg_network_summary.json`
- Run Dolphin
  - Input: Dolphin YAML + CSLC list
  - Output: wrapped/unwrapped phase, timeseries, velocity rasters under `stack/dolphin/`
  - If enabled: point products under `stack/dolphin/exports/` (`.csv`, `.kmz`, summary JSON)
- LOS decomposition (optional)
  - Input: ASC velocity raster + DSC velocity raster (+ optional coherence rasters + LOS projection coefficients)
  - Output: `stack/decomposition/east_velocity_m_per_year.tif`, `up_velocity_m_per_year.tif`, `valid_mask.tif`, `condition_number.tif`, optional `consistency_error_m_per_year.tif`, and `decomposition_summary.json`

## Orbit And Date Notes
Why: these are common first-run confusion points.

- COMPASS/S1Reader handles orbit retrieval automatically into `stack/orbits/`.
- Goal for best precision is POEORB; RESORB can be a temporary fallback.
- If prepare reports RESORB-only, rerun later to pick up POEORB when available.
- Wrapper treats `--end-date` as inclusive and adjusts COMPASS’s exclusive end-date behavior.

## Known Beginner Pitfalls
- `ModuleNotFoundError` right after installing package:
  - Usually caused by running `/bin/python3` instead of env Python.
- Running from wrong folder:
  - Always run commands from repo root and keep `--repo-root .`.
- Windows path confusion:
  - Prefer Linux path in WSL (`/home/...`) for files and performance.

## Related Docs
- Root setup: `README.md`
- Stack-local summary: `miami/insar/us_isleofnormandy_s1_asc_t48/README.md`
- Detailed runbooks: `knowledge_library/README.md`
