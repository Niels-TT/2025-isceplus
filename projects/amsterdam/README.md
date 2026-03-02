# amsterdam Workflow

This runbook is for the existing Amsterdam dual-track project.
Run all commands from repo root:

```bash
cd /home/niels/insar/git/2025-isceplus
```

## 1) Environment + Credentials
Why: most first-run failures are missing dependencies or credentials.

```bash
mamba env update -n isce3-feb -f envs/isce3-feb.yml
conda activate isce3-feb
python scripts/00_patch_rasterio_float16.py
bash scripts/00_check_credentials.sh
```

## 2) AOI + Config Sanity
Project AOI file:
- `projects/amsterdam/aux/bbox.kml`

Stack configs:
- `projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml`
- `projects/amsterdam/insar/amsterdam_s1_dsc_t000/config/processing_configuration.toml`

Before first search, check:
- `[aoi].buffer_m` is set (keep equal between ASC/DSC for consistent decomposition coverage).
- `[search].start` and `[search].end` are correct.
- `[search].flight_direction` is `ASCENDING` for ASC config and `DESCENDING` for DSC config.
- `[search].relative_orbit` and `[search].frame_number` are set from discovery.
- `[selection].require_reference = false` for first pass.
- `[search].expected_scenes = 0` and `[search].expected_unique_dates = 0` for first pass.

## 3) Optional Geometry Discovery (Orbit/Frame Selection)
Run once for this AOI/date window:

```bash
mamba run -n isce3-feb python scripts/02_discover_s1_candidates.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml \
  --min-unique-dates 1
```

Outputs:
- `projects/amsterdam/insar/amsterdam_s1_asc_t000/search/candidates/geometry_candidates.csv`
- `projects/amsterdam/insar/amsterdam_s1_asc_t000/search/candidates/geometry_candidates.json`
- `projects/amsterdam/insar/amsterdam_s1_asc_t000/search/candidates/stack_candidates.png`

Pick one ASC geometry and one DSC geometry, then update both config files with `relative_orbit` and `frame_number`.

## 4) Mandatory Stack Search (Creates `scenes.csv`)
Why: this creates deterministic scene manifests required by all downstream steps.

```bash
mamba run -n isce3-feb python scripts/03_search_s1_stack.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml

mamba run -n isce3-feb python scripts/03_search_s1_stack.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_dsc_t000/config/processing_configuration.toml
```

Check these files exist:
- `projects/amsterdam/insar/amsterdam_s1_asc_t000/search/products/scenes.csv`
- `projects/amsterdam/insar/amsterdam_s1_dsc_t000/search/products/scenes.csv`

## 5) Suggest + Set Reference Dates
Why: reference-date choice affects inversion conditioning and baseline spread.

```bash
mamba run -n isce3-feb python scripts/04_suggest_reference_date.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml

mamba run -n isce3-feb python scripts/04_suggest_reference_date.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_dsc_t000/config/processing_configuration.toml
```

Then update `[search].reference_date` in both config files.

Optional hardening after first pass:
- Set `[selection].require_reference = true`.
- Set `[search].expected_scenes` and `[search].expected_unique_dates` to exact values.
- Re-run step 4 to verify deterministic counts.

## 6) Run Main Pipeline Per Stack
Run ASC first, then DSC.

ASC stack:
```bash
# Download plan (dry-run)
mamba run -n isce3-feb python scripts/05_download_s1_stack.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml

# Download files
mamba run -n isce3-feb python scripts/05_download_s1_stack.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml \
  --download

# DEM
mamba run -n isce3-feb python scripts/06_download_dem_opentopography.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml

# COMPASS prepare + run
mamba run -n isce3-feb python scripts/07_prepare_compass_stack.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml

mamba run -n isce3-feb python scripts/08_run_compass_runfiles.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml

# Dolphin prepare + run
mamba run -n isce3-feb python scripts/09_prepare_dolphin_workflow.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml

mamba run -n isce3-feb python scripts/11_run_dolphin_workflow.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml
```

DSC stack:
```bash
# Download plan (dry-run)
mamba run -n isce3-feb python scripts/05_download_s1_stack.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_dsc_t000/config/processing_configuration.toml

# Download files
mamba run -n isce3-feb python scripts/05_download_s1_stack.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_dsc_t000/config/processing_configuration.toml \
  --download

# DEM
mamba run -n isce3-feb python scripts/06_download_dem_opentopography.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_dsc_t000/config/processing_configuration.toml

# COMPASS prepare + run
mamba run -n isce3-feb python scripts/07_prepare_compass_stack.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_dsc_t000/config/processing_configuration.toml

mamba run -n isce3-feb python scripts/08_run_compass_runfiles.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_dsc_t000/config/processing_configuration.toml

# Dolphin prepare + run
mamba run -n isce3-feb python scripts/09_prepare_dolphin_workflow.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_dsc_t000/config/processing_configuration.toml

mamba run -n isce3-feb python scripts/11_run_dolphin_workflow.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_dsc_t000/config/processing_configuration.toml
```

Notes:
- `07_prepare_compass_stack.py` auto-downloads the OPERA burst DB to `processing.compass.burst_db_file` when missing.
- If `[processing.dolphin.qc].enabled = true`, `09_prepare_dolphin_workflow.py` writes network QC outputs automatically.

## 7) Optional Decomposition (After ASC + DSC Dolphin)

```bash
mamba run -n isce3-feb python scripts/90_decompose_los_velocity.py \
  --repo-root . \
  --config projects/amsterdam/insar/amsterdam_s1_asc_t000/config/processing_configuration.toml
```

You can also pass the DSC config; decomposition config blocks are cross-wired to ASC+DSC velocity outputs.

## 8) Troubleshooting
- `04_suggest_reference_date.py` says `Missing scenes CSV`:
  run step 4 first.
- `03_search_s1_stack.py` returns `Selected scenes: 0`:
  AOI or geometry selection is wrong; re-check `projects/amsterdam/aux/bbox.kml`, then re-run step 3.
- `07_prepare_compass_stack.py` reports missing/incomplete local scenes:
  re-run step 6 download (`05_download_s1_stack.py --download`) for that stack.
- `08_run_compass_runfiles.py` reports missing run files:
  step 6 COMPASS prepare did not finish; fix that error and rerun.
