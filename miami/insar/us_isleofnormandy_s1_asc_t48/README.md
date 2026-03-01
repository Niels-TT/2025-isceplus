# us_isleofnormandy_s1_asc_t48

Project-specific stack workspace for the Miami AOI.

## Prerequisites
Why: scripts are configured for WSL2/Linux + conda environment execution.

- Run from repo root in WSL2/Linux.
- Activate environment:

```bash
conda activate isce3-feb
```

- Use `python ...` from that env (or `mamba run -n isce3-feb python ...`), not `/bin/python3`.

## Current Stack Definition
- Config file: `config/processing_configuration.toml`
- AOI source: `miami/aux/bbox.kml`
- AOI processing buffer: `aoi.buffer_m = 3000.0` m
- Final Dolphin crop: `processing.dolphin.crop_to_project_aoi = true`
- Search outputs: `search/`
- Selection policy: first 20 dates from reference `2015-09-21`
- Selected span: `2015-09-21` to `2017-03-26`
- Approx input volume: `92.36 GB` (20 Sentinel-1 IW SLC scenes)

## Run Order
Why: each stage produces required inputs for the next stage.

From repo root (`/home/niels/insar/git/2025-isceplus`):

1. Download DEM:

```bash
mamba run -n isce3-feb python scripts/06_download_dem_opentopography.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

2. Prepare COMPASS:

```bash
mamba run -n isce3-feb python scripts/07_prepare_compass_stack.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

3. Run COMPASS coregistration:

```bash
mamba run -n isce3-feb python scripts/08_run_compass_runfiles.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

4. Prepare Dolphin:

```bash
mamba run -n isce3-feb python scripts/09_prepare_dolphin_workflow.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

5. Run Dolphin (+ optional point export if enabled in TOML):

```bash
mamba run -n isce3-feb python scripts/11_run_dolphin_workflow.py \
  --repo-root . \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

## Key Folders
- `stack/slc/`: raw SLC ZIP files
- `stack/dem/`: DEM and metadata
- `stack/orbits/`: orbit cache (auto-managed by COMPASS/S1Reader)
- `stack/compass/`: runconfigs, runfiles, CSLC outputs, run state
- `stack/dolphin/`: Dolphin configs and time-series outputs
- `logs/`: execution logs
