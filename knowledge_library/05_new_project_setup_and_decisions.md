# 05 - New Project Setup And Decisions

Why: this runbook turns the pipeline into a reusable workflow for any AOI, not only Miami.

## 1) Bootstrap A New Project
From repo root:

```bash
python scripts/01_create_project_from_example.py \
  --repo-root . \
  --project-name my_city
```

This creates:
- `projects/my_city/aux/bbox.kml`
- `projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml`
- `projects/my_city/.gitignore` (keeps heavy generated outputs out of git)
- empty output folders with `.gitkeep`

Why: bootstrap script keeps all path references consistent and avoids manual copy mistakes.

## 2) Replace AOI KML
- Create AOI polygon in Google Earth Pro.
- Save as KML (not KMZ).
- KML coordinates are lon/lat in WGS84 (`EPSG:4326`).
- Replace `projects/my_city/aux/bbox.kml`.

Why: AOI controls ASF search geometry, DEM bounds, and downstream crop window.

Dual-track option (recommended if you plan LOS decomposition):

```bash
python scripts/01_create_project_from_example.py \
  --repo-root . \
  --project-name my_city \
  --dual-track
```

Why: this creates separate ASC and DSC stack configs up front, which matches how the
pipeline works (`flight_direction` is one value per stack config).

## 3) Discover Candidate Geometry
Before fixing `relative_orbit`, inspect available geometry groups.
For dual-track projects with a shared AOI/date window, run discovery once and
use the same ranked output to choose both ASC and DSC geometry:

```bash
mamba run -n isce3-feb python scripts/02_discover_s1_candidates.py \
  --repo-root . \
  --config projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml
```

Review the ranked table (direction/orbit/frame) and choose the best temporal coverage.
Also inspect:
- `.../search/candidates/stack_candidates.png`
for AOI vs discovered stack footprint overlap.

Why: choosing geometry first prevents weak or fragmented stacks.
If direction/orbit/frame is already known, you can skip discovery and proceed
directly to stack search.

Burst-level detail:
- discovery script ranks scene/frame geometry (pre-download)
- common-burst filtering happens in COMPASS (`common_bursts_only = true`)
- this is the normal practical sequence for stack stability

## 4) Edit Config First Pass
Edit:
`projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml`

Set at minimum:
- `[search] flight_direction`
- `[search] relative_orbit`
- `[search] start`
- `[search] end`
- `[selection] max_dates`

Set conservative expectations while exploring:
- `expected_scenes = 0`
- `expected_unique_dates = 0`

Why: `0` disables count checks while exploring; set values >0 after finalizing stack settings to enforce deterministic checks.

## 5) Run Search
```bash
mamba run -n isce3-feb python scripts/03_search_s1_stack.py \
  --repo-root . \
  --config projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml
```

Why: this freezes deterministic scene/date manifests before any large download.

## 6) Suggest Reference Date
Prerequisite: run stack search first so `search/products/scenes.csv` exists.

```bash
mamba run -n isce3-feb python scripts/04_suggest_reference_date.py \
  --repo-root . \
  --config projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml
```

Output includes:
- recommended heuristic date
- earliest and center alternatives
- ranked candidates JSON

Why: reference date affects both temporal network conditioning and perpendicular-baseline spread around the stack.

## 7) Lock Expected Counts
After finalizing date selection and `max_dates`, update:
- `expected_scenes`
- `expected_unique_dates`

Why: now the config can fail fast on accidental search drift.

## 8) Continue Standard Pipeline
Use your project config in all commands:
1. `05_download_s1_stack.py` (dry-run, then `--download`)
2. `06_download_dem_opentopography.py`
3. `07_prepare_compass_stack.py`
4. `08_run_compass_runfiles.py`
5. `09_prepare_dolphin_workflow.py`
6. `11_run_dolphin_workflow.py`

Why: each stage consumes outputs from the previous stage, preserving reproducibility.

QC note:
- Dolphin prepare now writes `ifg_network.png` and `ifg_network_summary.json`\n  when `[processing.dolphin.qc].enabled = true`.
- Use these to check if network connectivity/edge density is sensible before trusting results.

## Decision Boundaries (Practical)
- Geometry choice: manual decision, supported by `02_discover_s1_candidates.py`.
- Reference date: baseline-aware heuristic suggestion from `04_suggest_reference_date.py`, final decision is manual.
- Dolphin tuning: always iterative per AOI noise/coherence behavior.

Why: these decisions depend on local scene quality and analysis objective; no default can be universally optimal.
