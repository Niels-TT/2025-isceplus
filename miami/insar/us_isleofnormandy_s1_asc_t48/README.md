# us_isleofnormandy_s1_asc_t48

## Current State
- Stack config is in `config/stack.toml`
- ASF query output is in `search/`
- `search/products/scene_names.txt` contains 20 S1 IW SLC scenes
- Selection policy: first 20 dates from reference date (`2015-09-21`)
- Selected span: `2015-09-21` to `2017-03-26`
- Selected source volume: about `92.36 GB` (decimal)

## Next Processing Stages
1. Download SLC + orbit files into a dedicated raw-data location.
2. Build CSLC/SLC stack directory layout under `stack/`.
3. Coregister stack (isce3 workflow).
4. Run time-series pipeline (dolphin) on the coregistered stack.

## Notes
- Keep processing logs in `logs/`
- Use `scratch/` for temporary intermediates that can be regenerated
