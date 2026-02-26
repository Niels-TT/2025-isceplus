# Miami InSAR Project

## Structure
- `aux/`
- `aux/bbox.kml`: AOI polygon (source of truth)
- `bbox.kml`: symlink to `aux/bbox.kml` for convenience
- `insar/us_isleofnormandy_s1_asc_t48/`
- `insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml`: stack + processing config
- `insar/us_isleofnormandy_s1_asc_t48/search/`: ASF search outputs
- `insar/us_isleofnormandy_s1_asc_t48/stack/slc/`: downloaded Sentinel-1 ZIPs
- `insar/us_isleofnormandy_s1_asc_t48/stack/dem/`: AOI DEM from OpenTopography
- `insar/us_isleofnormandy_s1_asc_t48/stack/orbits/`: POEORB/RESORB cached by COMPASS
- `insar/us_isleofnormandy_s1_asc_t48/stack/compass/`: COMPASS runconfigs/runfiles/results
- `insar/us_isleofnormandy_s1_asc_t48/logs/`: execution logs

## Stack Definition
- AOI: `miami/aux/bbox.kml`
- Time range: `2015-09-21` to `2022-04-30`
- Reference date: `2015-09-21`
- Sensor: Sentinel-1 IW SLC
- Direction/Track: Ascending / Relative Orbit 48
- Selection policy: first `20` acquisition dates starting at reference date

## Scripts
- `miami/scripts/search_s1_stack.py`: ASF search + scene manifest generation
- `miami/scripts/download_s1_stack.py`: storage-aware SLC downloader with tqdm
- `miami/scripts/download_dem_opentopography.py`: AOI DEM downloader from OpenTopography
- `miami/scripts/prepare_compass_stack.py`: builds COMPASS run files (with integrated orbit retrieval)
- `miami/scripts/run_compass_runfiles.py`: executes generated COMPASS run files with resume state
- `miami/scripts/prepare_dolphin_workflow.py`: validates COMPASS CSLC outputs and writes Dolphin config
- `miami/scripts/run_dolphin_workflow.py`: executes `dolphin run` from prepared YAML config

## Alignment With Official Workflows
Why: this clarifies what is standard OPERA/ISCE3 workflow vs project-specific orchestration.

- Core stack geocode/coreg processing is official COMPASS (`s1_geocode_stack.py`), not custom reimplementation.
- Orbit retrieval is official `s1-reader` behavior used by COMPASS (auto lookup/download when needed).
- Time-series stage target is Dolphin on top of coregistered stack products.
- Low-level SAR math/geometry engine is ISCE3 under these higher-level tools.
- Scene discovery and metadata are built from ASF Search API (`asf_search` package).

Project-specific parts in this repo:
- `search_s1_stack.py` defines AOI/date selection policy and writes reproducible scene manifests.
- `download_s1_stack.py` adds storage checks, resumable manifesting, and terminal progress UX.
- `prepare_compass_stack.py` validates input completeness and builds a deterministic COMPASS command.
- `run_compass_runfiles.py` adds resumable runfile execution and logging around COMPASS outputs.

Reference repos/docs:
- COMPASS: https://github.com/opera-adt/COMPASS
- COMPASS docs: https://opera-compass.readthedocs.io/en/latest/
- s1-reader: https://github.com/opera-adt/s1-reader
- Dolphin: https://github.com/isce-framework/dolphin
- Dolphin docs: https://dolphin-insar.readthedocs.io/
- ISCE3: https://github.com/isce-framework/isce3
- ASF Search docs: https://docs.asf.alaska.edu/asf_search/

## Environment
Why: COMPASS CLI is required for stack coregistration.

```bash
mamba env update -n isce3-feb -f /home/niels/course/2025-isceplus/envs/isce3-feb.yml
```

## Why CLI + `--repo-root` + `--config`
Why: explicit paths make every run reproducible and avoid hidden working-directory behavior.

- `--config` picks the exact stack definition.
- `--repo-root` forces stable relative-path resolution.
- Commands can be copy-pasted into notes/issues and replayed exactly.
- This README is the canonical command source for this project.

## Step Inputs/Outputs
Why: concise I/O mapping makes each stage auditable and easier to debug.

- Search (`search_s1_stack.py`)
  - Inputs: `processing_configuration.toml` search/AOI settings + `miami/aux/bbox.kml`
  - Outputs: `search/products/scenes.csv`, `scene_names.txt`, `summary.json`, `search/raw/results.geojson`, `aoi.wkt`
- Download SLC (`download_s1_stack.py`)
  - Inputs: `search/products/scenes.csv`, Earthdata credentials
  - Outputs: `stack/slc/*.zip`, `stack/download_manifest.json`
- Download DEM (`download_dem_opentopography.py`)
  - Inputs: AOI KML + DEM settings + OpenTopography key
  - Outputs: `stack/dem/*.tif` (+ metadata json)
- Prepare COMPASS (`prepare_compass_stack.py`)
  - Inputs: `stack/slc/*.zip`, DEM, stack config, burst DB path
  - Outputs: `stack/compass/runconfigs/*.yaml`, `run_files/*.sh`, `prepare_summary.json`
- Run COMPASS (`run_compass_runfiles.py`)
  - Inputs: runfiles + runconfigs
  - Outputs: coregistered CSLC HDF5 per burst/date under `stack/compass/<burst_id>/<YYYYMMDD>/*.h5`, logs, `run_state.json`
- Prepare Dolphin (`prepare_dolphin_workflow.py`)
  - Inputs: COMPASS CSLC HDF5 outputs + AOI/config
  - Outputs: `stack/dolphin/inputs/cslc_files.txt`, `stack/dolphin/config/dolphin_config.yaml`, `stack/dolphin/prepare_summary.json`
- Run Dolphin (`run_dolphin_workflow.py`)
  - Inputs: Dolphin config YAML + CSLC list
  - Outputs: displacement workflow products under `stack/dolphin/` (wrapped phase, unwrap, timeseries, velocity)

## Download Stage
Why: geocoding/coregistration cannot start until the selected raw SLC ZIPs are local.

Dry-run (size + free-space check):

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

Actual download:

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml \
  --download
```

## Post-Download COMPASS Workflow
Why: this is the minimum complete path to high-precision stack coregistration with isce3/COMPASS.

1. Validate credentials:
   Why: Earthdata is needed for ASF SLC access and `.topoapi` for DEM API calls.

```bash
bash /home/niels/course/2025-isceplus/scripts/check_credentials.sh
```

2. Download DEM for AOI:
   Why: DEM is required for geometric geocoding/coregistration.

```bash
mamba run -n isce3-feb python miami/scripts/download_dem_opentopography.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

This stack config uses `SRTMGL1_E` and sets `vertical_datum = WGS84_ELLIPSOID`.
Why: this makes DEM height reference explicit and avoids silent datum mismatch in high-precision geometry.

3. Prepare COMPASS stack run files:
   Why: this step scans SLCs, applies AOI/date settings, and generates per-burst run scripts.

```bash
mamba run -n isce3-feb python miami/scripts/prepare_compass_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

Notes:
- Orbit retrieval is integrated: missing orbit files are auto-downloaded by COMPASS/S1Reader into `stack/orbits/`.
- Prepare output reports orbit cache counts and status (`POEORB` vs `RESORB`) after run-file generation.
- End date is treated as inclusive in this wrapper; it passes `end_date + 1 day` to COMPASS `-ed` (exclusive bound) so the last acquisition date is included.
- AOI `bbox` is always passed to keep the workflow portable (avoids dependency on private default burst DB paths).
- Scene completeness is checked before prepare: missing ZIPs and wrong-size ZIPs both stop the run by default.

4. Run generated COMPASS run files:
   Why: this executes the actual geocoding/coregistration workloads.

```bash
mamba run -n isce3-feb python miami/scripts/run_compass_runfiles.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

Resume behavior:
- `run_compass_runfiles.py` writes `stack/compass/run_state.json`.
- Re-running resumes and skips completed run files.
- Use `--no-resume` to force re-run.

## Useful Dry Runs
Why: fail fast before heavy compute.

Prepare dry-run (show exact COMPASS command):

```bash
mamba run -n isce3-feb python miami/scripts/prepare_compass_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml \
  --dry-run
```

Run-files dry-run (show pending work):

```bash
mamba run -n isce3-feb python miami/scripts/run_compass_runfiles.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml \
  --dry-run
```

## Orbit And Date Notes
Why: these two details commonly cause confusion in first stack runs.

- If you see `No single orbit file was found`, check the final prepare summary lines.
- High-precision target: `Orbit status: precise POEORB files are being used.`
- If prepare reports `RESORB-only fallback`, rerun later so POEORB can become available in cache.
- Date overrides (`--start-date`, `--end-date`) are `YYYYMMDD`; `--end-date` is interpreted inclusively by the wrapper.

## Coreg Output Shape
Why: this avoids confusion about whether COMPASS makes one monolithic stack file.

- COMPASS does not create a single all-dates HDF5 stack.
- It creates one CSLC HDF5 per acquisition date per burst.
- Time-series stacking/inversion happens in Dolphin after these CSLC files exist.

## Dolphin Stage (After COMPASS)
Why: Dolphin is the time-series InSAR stage on top of COMPASS coregistered CSLCs.

Sensible run order from your current state:

1. Dry-run prepare (validate CSLCs + print exact Dolphin command):

```bash
mamba run -n isce3-feb python miami/scripts/prepare_dolphin_workflow.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml \
  --dry-run
```

2. Prepare for real (writes CSLC list + Dolphin YAML config):

```bash
mamba run -n isce3-feb python miami/scripts/prepare_dolphin_workflow.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

3. Optional: inspect/tweak Dolphin config behavior:
- Edit TOML under `[processing.dolphin]` for common knobs.
- For low-memory machines, lower `worker_block_shape` and `timeseries_block_shape` (for example `[128, 128]`).
- Use `[processing.dolphin.option_overrides]` for any Dolphin flag/value.
- Rerun step 2 after changes to regenerate YAML deterministically.

4. Run Dolphin workflow:

```bash
mamba run -n isce3-feb python miami/scripts/run_dolphin_workflow.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml
```

5. Optional troubleshooting run (more verbose logs):

```bash
mamba run -n isce3-feb python miami/scripts/run_dolphin_workflow.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml \
  --debug
```

Notes:
- The Dolphin prep script checks CSLC validity by opening each HDF5 and verifying the configured subdataset (default `data/VV`).
- CSLC discovery uses `processing.dolphin.cslc_glob` (strict by default) and only uses recursive `**/*.h5` fallback if `allow_recursive_cslc_search=true`.
- By default it fails if valid CSLC count is below `expected_unique_dates` from stack search (use `--allow-partial-cslc` to override).
- It also writes `stack/dolphin/config/dolphin_config.reference.yaml` (from `dolphin config --print-empty`) so you can inspect the full option space.

How to discover/tune Dolphin options:
- Quick CLI inventory: `mamba run -n isce3-feb dolphin config --help`
- Full default template YAML: `mamba run -n isce3-feb dolphin config --print-empty --outfile /tmp/dolphin_empty.yaml`
- Project-level controls live in `[processing.dolphin]` inside `config/processing_configuration.toml`.
- Any Dolphin option can be passed via `[processing.dolphin.option_overrides]` (bool/scalar/list).
- `processing_configuration.toml` now includes a commented advanced catalog of currently unmapped Dolphin options you can enable in-place.
- For any CLI flag not explicitly mapped by the wrapper, use `processing.dolphin.extra_cli_args`.
- Overlap guard: wrapper-managed flags must not be duplicated in `option_overrides` or `extra_cli_args`; prepare now fails fast on conflicts.
