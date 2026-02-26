# Post-Download Preprocessing Playbook (DEM + COMPASS)

## Goal
Take a downloaded Sentinel-1 SLC stack and run the preprocessing stage needed before time-series analysis.

## Why This Matters
Most hard failures happen between download and coregistration (missing scenes, wrong DEM datum, orbit/coverage mismatch), so this stage should be explicit and validated.

## Official Workflow Alignment
Why: this confirms we are using recommended tooling, not reimplementing SAR processing.

- Core processing command is `s1_geocode_stack.py` from COMPASS.
- Orbit retrieval is handled by COMPASS/s1-reader.
- ISCE3 provides the low-level processing engine.
- Our local scripts add orchestration, validation, and reproducible command wiring.

## Required Inputs
- Downloaded SLC ZIPs in `stack/slc/`
- Scene manifest from search stage (`search/products/scenes.csv`)
- DEM config in `stack.toml` under `[ancillary.dem]`
- Credentials:
  - `~/.netrc` for Earthdata
  - `~/.topoapi` for OpenTopography

## Step 0: Ensure Environment
Why: COMPASS CLI is required for the prepare step.

```bash
mamba env update -n isce3-feb -f /home/niels/course/2025-isceplus/envs/isce3-feb.yml
```

## Step 1: Validate Credentials
Why: fail early before long downloads/runs.

```bash
bash /home/niels/course/2025-isceplus/scripts/check_credentials.sh
```

## Step 2: Download DEM (Ellipsoidal Heights)
Why: geometric coregistration depends on DEM and vertical datum consistency.

```bash
mamba run -n isce3-feb python /home/niels/course/2025-isceplus/miami/scripts/download_dem_opentopography.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
```

Current recommended config (in `stack.toml`):
- `demtype = "SRTMGL1_E"`
- `vertical_datum = "WGS84_ELLIPSOID"`
- `require_ellipsoid_heights = true`

## Step 3: Prepare COMPASS Run Files (Dry-Run First)
Why: verify command, AOI bbox, scene completeness, and datum checks before processing.

```bash
mamba run -n isce3-feb python /home/niels/course/2025-isceplus/miami/scripts/prepare_compass_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml \
  --dry-run
```

What this validate/prepare step enforces:
- missing SLC ZIP detection
- size-mismatch SLC detection (partial or corrupt downloads)
- explicit DEM vertical datum check
- deterministic COMPASS command generation with AOI bbox
- inclusive end-date handling (wrapper passes `end_date + 1 day` to COMPASS `-ed`, which is exclusive)
- run file generation under `stack/compass/run_files/`
- orbit cache summary output (`POEORB`/`RESORB`) after prepare run

Then run prepare for real:

```bash
mamba run -n isce3-feb python /home/niels/course/2025-isceplus/miami/scripts/prepare_compass_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
```

## Step 4: Execute COMPASS Run Files
Why: this runs the actual preprocessing/coregistration jobs.

```bash
mamba run -n isce3-feb python /home/niels/course/2025-isceplus/miami/scripts/run_compass_runfiles.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
```

Resume behavior:
- state file: `stack/compass/run_state.json`
- logs: `logs/compass/`
- rerun continues from pending jobs by default
- use `--no-resume` to force rerun all

## About `common_bursts_only`
Why: burst selection strongly affects stack consistency.

- `common_bursts_only = true` keeps only burst IDs present across all dates.
- For small AOIs fully inside one stable burst, this is usually the safest default.
- Use explicit burst IDs only when you intentionally need manual burst control.

## Handoff to Time-Series Stage
Why: Dolphin expects coregistered stack products as input.

After COMPASS runfiles complete cleanly, move to Dolphin pipeline configuration and execution.

## Quick Troubleshooting
- `Missing command: s1_geocode_stack.py`:
  - update/install env and re-run with `mamba run -n isce3-feb ...`
- `Scene completeness check failed`:
  - let downloader finish, then rerun prepare
- `Vertical datum check failed`:
  - set correct values in `[ancillary.dem]` in `stack.toml`
- `No single orbit file was found` warning:
  - this can appear before orbit fallback selection completes
  - verify final prepare output shows `Orbit status: precise POEORB files are being used.`
- Last acquisition date missing from run files:
  - ensure you use the updated `prepare_compass_stack.py` that treats `--end-date` as inclusive
- Runfile failure:
  - inspect corresponding log under `logs/compass/`
