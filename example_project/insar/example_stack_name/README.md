# __STACK_NAME__ Workspace

Why: this directory keeps all generated artifacts for one stack, separate from scripts and source config.

## Layout
- `config/processing_configuration.toml`: project stack configuration (single source of truth)
- `search/`: ASF query outputs (`scenes.csv`, manifests)
- `stack/slc/`: downloaded raw SLC ZIPs
- `stack/dem/`: DEM and DEM metadata
- `stack/orbits/`: orbit cache
- `stack/compass/`: COMPASS runfiles/config/CSLC outputs
- `stack/dolphin/`: Dolphin config and displacement products
- `logs/`: execution logs

## First Edits
1. Update `config/processing_configuration.toml` with your date range and geometry.
2. Replace `../../aux/bbox.kml` with your AOI.
3. Run discovery/search before downloading.
