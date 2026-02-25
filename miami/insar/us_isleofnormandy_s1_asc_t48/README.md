# us_isleofnormandy_s1_asc_t48

## Current State
- Stack config is in `config/stack.toml`
- ASF query output is in `search/`
- `search/products/scene_names.txt` contains 161 S1 IW SLC scenes

## Next Processing Stages
1. Download SLC + orbit files into a dedicated raw-data location.
2. Build CSLC/SLC stack directory layout under `stack/`.
3. Coregister stack (isce3 workflow).
4. Run time-series pipeline (dolphin) on the coregistered stack.

## Notes
- Keep processing logs in `logs/`
- Use `scratch/` for temporary intermediates that can be regenerated
