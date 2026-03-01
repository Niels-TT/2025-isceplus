# Example Project Template

Why: this folder is a reusable scaffold for any local InSAR project using the same pipeline (`asf_search -> download -> DEM -> COMPASS -> Dolphin`).

## Prerequisites
1. Use WSL2/Linux and open the repo from a Linux path (`/home/...`).
2. Install/update the conda environment and activate it:

```bash
mamba env update -n isce3-feb -f envs/isce3-feb.yml
conda activate isce3-feb
```

3. Confirm credentials:

```bash
bash scripts/00_check_credentials.sh
```

## Quick Start
1. Create a new project from this template:

```bash
python scripts/01_create_project_from_example.py \
  --repo-root . \
  --project-name my_city
```

Why: this avoids manual path mistakes and keeps all config references consistent.

2. Open your new project folder (default location: `projects/my_city`).

3. Replace `projects/my_city/aux/bbox.kml` with your AOI KML from Google Earth Pro.

Why: the AOI KML is the source of truth for search, DEM bounds, and processing crop.

4. Edit:
`projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml`

Set at minimum:
- `[search] start`, `end`
- `[search] relative_orbit` (after discovery)
- `[search] reference_date` (after stack search + suggestion)
- `[selection] max_dates`

## Geometry Discovery (Before Final Orbit Choice)
Run candidate discovery before locking direction/orbit/frame:

```bash
mamba run -n isce3-feb python scripts/02_discover_s1_candidates.py \
  --repo-root . \
  --config projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml
```

Why: this ranks available acquisition geometries by temporal coverage so you can choose the most stable stack setup.
It also writes a visual map:
- `.../search/candidates/geometry_candidates_map.png`
showing AOI vs all discovered stack footprints.

After choosing geometry, update `[search]` fields:
- `flight_direction`
- `relative_orbit`

Burst note:
- This discovery stage is scene/frame-level.
- Burst-level intersection is resolved during COMPASS preparation via `common_bursts_only=true`.
- For small AOIs, this is usually the safest approach because all dates are trimmed to common bursts automatically.

## Reference Date Suggestion
After running stack search once, suggest a robust reference date:

```bash
mamba run -n isce3-feb python scripts/04_suggest_reference_date.py \
  --repo-root . \
  --config projects/my_city/insar/my_city_s1_asc_t000/config/processing_configuration.toml
```

Why: this recommends dates with strong temporal support and lower edge-risk; it is a practical heuristic, not a full geophysical quality metric.

## Full Run Order (Any Project)
Use your own config path in every command.

1. Search scenes:
```bash
mamba run -n isce3-feb python scripts/03_search_s1_stack.py \
  --repo-root . \
  --config <your_config.toml>
```

2. Optional: reference-date suggestion (after search):
```bash
mamba run -n isce3-feb python scripts/04_suggest_reference_date.py \
  --repo-root . \
  --config <your_config.toml>
```

3. Download SLCs (dry-run first, then real run with `--download`):
```bash
mamba run -n isce3-feb python scripts/05_download_s1_stack.py \
  --repo-root . \
  --config <your_config.toml>

mamba run -n isce3-feb python scripts/05_download_s1_stack.py \
  --repo-root . \
  --config <your_config.toml> \
  --download
```

4. Download DEM:
```bash
mamba run -n isce3-feb python scripts/06_download_dem_opentopography.py \
  --repo-root . \
  --config <your_config.toml>
```

5. Prepare + run COMPASS coreg:
```bash
mamba run -n isce3-feb python scripts/07_prepare_compass_stack.py \
  --repo-root . \
  --config <your_config.toml>

mamba run -n isce3-feb python scripts/08_run_compass_runfiles.py \
  --repo-root . \
  --config <your_config.toml>
```

6. Prepare + run Dolphin:
```bash
mamba run -n isce3-feb python scripts/09_prepare_dolphin_workflow.py \
  --repo-root . \
  --config <your_config.toml>

mamba run -n isce3-feb python scripts/11_run_dolphin_workflow.py \
  --repo-root . \
  --config <your_config.toml>
```

7. Optional dual-track decomposition (after both ASC + DSC Dolphin runs exist):
```bash
mamba run -n isce3-feb python scripts/90_decompose_los_velocity.py \
  --repo-root . \
  --config <your_config.toml>
```

Why: this estimates East/Up velocity by solving the dual-geometry LOS system on a common raster grid.

During Dolphin prepare, QC outputs are created automatically when `[processing.dolphin.qc].enabled = true`:
- `.../stack/dolphin/qc/ifg_network.png`
- `.../stack/dolphin/qc/ifg_network_summary.json`

Why: this lets you inspect network connectivity before full displacement analysis.

## Notes On What Is Automatic vs Manual
- Automatic:
  - KML to AOI bounds/WKT
  - ASF search outputs
  - SLC download + resume manifest
  - DEM retrieval
  - Orbit retrieval via COMPASS/S1Reader
  - COMPASS runfile generation/execution
  - Dolphin config/run and exports
- Manual decisions:
  - Geometry choice (direction/orbit/frame)
  - Reference date final selection
  - Dolphin parameter tuning per AOI/noise regime

Why: these decisions depend on local context and quality goals; forcing them to defaults can reduce product quality.
