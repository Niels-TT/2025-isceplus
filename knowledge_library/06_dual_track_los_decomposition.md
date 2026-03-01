# 06 - Dual-Track LOS Decomposition (ASC + DSC -> East + Up)

Why:
Dolphin gives line-of-sight (LOS) velocity rasters. Dual-track decomposition combines ascending + descending LOS into horizontal-east and vertical-up components for interpretation.

## What This Stage Is (And Is Not)
- Is: raster-based per-pixel linear decomposition.
- Is not: sparse PS/DS vector decomposition from a dedicated point-network solver.

This is still scientifically valid when geometry and masking are handled carefully, but output remains raster products.

## Inputs You Need
1. Ascending LOS velocity raster (from Dolphin): `.../stack/dolphin/timeseries/velocity.tif`
2. Descending LOS velocity raster (from a second project run)
3. Optional temporal coherence rasters for ASC/DSC
4. LOS projection coefficients for each track:
   - `los_east_coeff`
   - `los_up_coeff`
   - Recommended: set both to `"auto"` to derive from COMPASS incidence/heading rasters.

Model per pixel:
- `v_los_asc = a_e * v_east + a_u * v_up`
- `v_los_dsc = d_e * v_east + d_u * v_up`

The script solves this 2x2 system for `v_east` and `v_up`.

## Configure In `processing_configuration.toml`
Use `[processing.decomposition]` and track tables:
- `[processing.decomposition.track_asc]`
- `[processing.decomposition.track_dsc]`

Coefficient mode:
- Auto: set both `los_east_coeff = "auto"` and `los_up_coeff = "auto"` for each track.
- Manual: provide both as numeric values.

Key controls:
- `enabled`: master switch
- `run_after_dolphin`: auto-run at end of Dolphin wrapper
- `target_grid`: `asc` or `dsc`
- `min_temporal_coherence`: quality threshold (when coherence files exist)
- `max_condition_number`: geometry safety check

## Run It
Manual run:

```bash
mamba run -n isce3-feb python scripts/90_decompose_los_velocity.py \
  --repo-root . \
  --config <your_config.toml>
```

Or set:
- `enabled = true`
- `run_after_dolphin = true`

Then run Dolphin wrapper normally and decomposition is appended automatically.

## Outputs
Written to `[processing.decomposition].output_dir`:
- `east_velocity_m_per_year.tif`
- `up_velocity_m_per_year.tif`
- `valid_mask.tif`
- `condition_number.tif`
- `consistency_error_m_per_year.tif` (if enabled)
- `decomposition_summary.json`

## Quality Checklist
1. Check `decomposition_summary.json` for condition number and valid fraction.
2. Verify coherence threshold is not removing nearly all pixels.
3. Confirm coefficient sign convention is correct for your LOS product definition.
