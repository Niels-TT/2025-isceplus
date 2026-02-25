# Miami InSAR Project

## Structure
- `aux/`
- `aux/bbox.kml`: AOI polygon (source of truth)
- `bbox.kml`: symlink to `aux/bbox.kml` for convenience
- `insar/us_isleofnormandy_s1_asc_t48/`
- `insar/us_isleofnormandy_s1_asc_t48/config/stack.toml`: stack + processing config
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
- Dolphin: https://github.com/opera-adt/dolphin
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

## Download Stage
Why: geocoding/coregistration cannot start until the selected raw SLC ZIPs are local.

Dry-run (size + free-space check):

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
```

Actual download:

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml \
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
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
```

This stack config uses `SRTM_GL1_Ellip` and sets `vertical_datum = WGS84_ELLIPSOID`.
Why: this makes DEM height reference explicit and avoids silent datum mismatch in high-precision geometry.

3. Prepare COMPASS stack run files:
   Why: this step scans SLCs, applies AOI/date settings, and generates per-burst run scripts.

```bash
mamba run -n isce3-feb python miami/scripts/prepare_compass_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
```

Notes:
- Orbit retrieval is integrated: missing orbit files are auto-downloaded by COMPASS/S1Reader into `stack/orbits/`.
- AOI `bbox` is always passed to keep the workflow portable (avoids dependency on private default burst DB paths).
- Scene completeness is checked before prepare: missing ZIPs and wrong-size ZIPs both stop the run by default.

4. Run generated COMPASS run files:
   Why: this executes the actual geocoding/coregistration workloads.

```bash
mamba run -n isce3-feb python miami/scripts/run_compass_runfiles.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
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
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml \
  --dry-run
```

Run-files dry-run (show pending work):

```bash
mamba run -n isce3-feb python miami/scripts/run_compass_runfiles.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml \
  --dry-run
```
