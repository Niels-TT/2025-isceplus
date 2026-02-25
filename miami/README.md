# Miami InSAR Project

## Structure
- `aux/`
- `aux/bbox.kml`: AOI polygon (source of truth)
- `bbox.kml`: symlink to `aux/bbox.kml` for convenience
- `insar/us_isleofnormandy_s1_asc_t48/`
- `insar/us_isleofnormandy_s1_asc_t48/config/stack.toml`: stack/search configuration
- `insar/us_isleofnormandy_s1_asc_t48/search/`: ASF search outputs
- `insar/us_isleofnormandy_s1_asc_t48/stack/`: stack products (to be filled by processing)
- `insar/us_isleofnormandy_s1_asc_t48/logs/`
- `insar/us_isleofnormandy_s1_asc_t48/scratch/`

## Stack Definition
- AOI: `miami/aux/bbox.kml`
- Time range: `2015-09-21` to `2022-04-30`
- Reference date: `2015-09-21`
- Sensor: Sentinel-1 IW SLC
- Direction/Track: Ascending / Relative Orbit 48
- Selection policy: first `20` acquisition dates starting at reference date
- Expected selected count: `20` scenes, `20` unique dates
- Full-match context (before subsetting): `161` scenes, `161` unique dates

## Run Search
From repo root:

```bash
mamba run -n isce3-feb python miami/scripts/search_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
```

Outputs:
- `miami/insar/us_isleofnormandy_s1_asc_t48/search/products/scene_names.txt`
- `miami/insar/us_isleofnormandy_s1_asc_t48/search/products/scenes.csv`
- `miami/insar/us_isleofnormandy_s1_asc_t48/search/products/summary.json`
- `miami/insar/us_isleofnormandy_s1_asc_t48/search/raw/results.geojson`
- `miami/insar/us_isleofnormandy_s1_asc_t48/search/raw/aoi.wkt`
