# 05 - New Project Setup And Decisions

Why: this runbook turns the pipeline into a reusable workflow for any AOI, not only Miami.

## 1) Bootstrap A New Project
From repo root:

```bash
python scripts/create_project_from_example.py \
  --repo-root . \
  --project-name my_city
```

This creates:
- `projects/my_city/aux/bbox.kml`
- `projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml`
- empty output folders with `.gitkeep`

Why: bootstrap script keeps all path references consistent and avoids manual copy mistakes.

## 2) Replace AOI KML
- Create AOI polygon in Google Earth Pro.
- Save as KML (not KMZ).
- Replace `projects/my_city/aux/bbox.kml`.

Why: AOI controls ASF search geometry, DEM bounds, and downstream crop window.

## 3) Discover Candidate Geometry
Before fixing `relative_orbit`, inspect available geometry groups:

```bash
mamba run -n isce3-feb python scripts/discover_s1_candidates.py \
  --repo-root . \
  --config projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml
```

Review the ranked table (direction/orbit/frame) and choose the best temporal coverage.

Why: choosing geometry first prevents weak or fragmented stacks.

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

Why: avoid hard failures while still validating path/search logic.

## 5) Run Search
```bash
mamba run -n isce3-feb python miami/scripts/search_s1_stack.py \
  --repo-root . \
  --config projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml
```

Why: this freezes deterministic scene/date manifests before any large download.

## 6) Suggest Reference Date
```bash
mamba run -n isce3-feb python scripts/suggest_reference_date.py \
  --repo-root . \
  --config projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml
```

Output includes:
- recommended heuristic date
- earliest and center alternatives
- ranked candidates JSON

Why: reference date affects network conditioning and inversion stability.

## 7) Lock Expected Counts
After finalizing date selection and `max_dates`, update:
- `expected_scenes`
- `expected_unique_dates`

Why: now the config can fail fast on accidental search drift.

## 8) Continue Standard Pipeline
Use your project config in all commands:
1. `download_s1_stack.py` (dry-run, then `--download`)
2. `download_dem_opentopography.py`
3. `prepare_compass_stack.py`
4. `run_compass_runfiles.py`
5. `prepare_dolphin_workflow.py`
6. `run_dolphin_workflow.py`

Why: each stage consumes outputs from the previous stage, preserving reproducibility.

## Decision Boundaries (Practical)
- Geometry choice: manual decision, supported by `discover_s1_candidates.py`.
- Reference date: heuristic suggestion from `suggest_reference_date.py`, final decision is manual.
- Dolphin tuning: always iterative per AOI noise/coherence behavior.

Why: these decisions depend on local scene quality and analysis objective; no default can be universally optimal.
