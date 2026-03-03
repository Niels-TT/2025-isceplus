# __STACK_NAME__ Workspace

Why: this directory keeps all generated artifacts for one stack, separate from scripts and source config.

## Layout
- `config/processing_configuration.toml`: project stack configuration (single source of truth)
- `search/`: ASF query outputs (`scenes.csv`, manifests, candidate and selected-stack maps)
  - Discovery candidates map: `search/candidates/stack_candidates.png`
  - Download stack map: `search/products/download_stack_footprint.png`
- `stack/slc/`: downloaded raw SLC ZIPs
- `stack/dem/`: DEM and DEM metadata
- `stack/orbits/`: orbit cache
- `stack/compass/`: COMPASS runfiles/config/CSLC outputs
- `stack/dolphin/`: Dolphin config and displacement products
- `logs/`: execution logs

## First Edits
1. Update `config/processing_configuration.toml` with:
   - date range + geometry (`flight_direction` is one direction per stack config)
   - `aoi.buffer_m` (processing/search buffer, default `3000.0` m)
   - `processing.dolphin.crop_to_project_aoi = true` (default; final outputs clipped to project AOI)
2. Replace `../../aux/bbox.kml` with your AOI.
3. Run discovery/search before downloading.
