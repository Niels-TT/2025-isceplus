# us_isleofnormandy_s1_asc_t48

## Current State
- Stack config is in `config/stack.toml`
- ASF query output is in `search/`
- `search/products/scene_names.txt` contains 20 S1 IW SLC scenes
- Selection policy: first 20 dates from reference date (`2015-09-21`)
- Selected span: `2015-09-21` to `2017-03-26`
- Selected source volume: about `92.36 GB` (decimal)

## Post-Download Plan
1. Download DEM to `stack/dem/` (`download_dem_opentopography.py`).
2. Prepare COMPASS run files (`prepare_compass_stack.py`).
3. Run generated run files (`run_compass_runfiles.py`).
4. Use resulting coregistered outputs for Dolphin processing.

## Notes
- Keep processing logs in `logs/`
- Use `scratch/` for temporary intermediates that can be regenerated
- Use `stack/slc/` for raw SAFE ZIP storage
- Use `stack/orbits/` for orbit cache (auto-filled by COMPASS/S1Reader)
- Use `stack/compass/` for generated runconfigs/runfiles/state
- `stack/download_manifest.json` records per-scene download status
