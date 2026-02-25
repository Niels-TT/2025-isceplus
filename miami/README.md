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
Why: this creates a reproducible, machine-readable scene manifest before any heavy download.

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

## Download Stack (SLC)
Why: this converts your selected scene list into local raw data for processing.

Dry-run first (size + free-space check only):

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
```

Execute actual download:

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml \
  --download
```

Optional staged download (example: first 5 pending scenes):

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml \
  --download --max-scenes 5
```

Files are stored in:
- `miami/insar/us_isleofnormandy_s1_asc_t48/stack/slc/`
- `miami/insar/us_isleofnormandy_s1_asc_t48/stack/download_manifest.json`

## Why CLI + `--repo-root` + `--config`
Why: explicit inputs make runs reproducible and avoid “it worked from one folder but not another”.

- `--config` selects the exact stack definition file
- `--repo-root` resolves all relative paths in config the same way every time
- command history can be copied into docs/issues and replayed later

## What To Do Now (Stack Images)
Why: this is the minimum reliable path to get the selected SLC stack locally.

1. Validate credentials:
   Why: downloads will fail immediately if Earthdata auth is missing.

```bash
bash /home/niels/course/2025-isceplus/scripts/check_credentials.sh
```

2. (Optional) regenerate search outputs:
   Why: confirms scene list matches current config before download.

```bash
mamba run -n isce3-feb python /home/niels/course/2025-isceplus/miami/scripts/search_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
```

3. Run downloader dry-run:
   Why: checks size/free-space without transferring data.

```bash
mamba run -n isce3-feb python /home/niels/course/2025-isceplus/miami/scripts/download_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml
```

4. Start with staged download (`5` scenes):
   Why: quick validation of auth/network/storage before full pull.

```bash
mamba run -n isce3-feb python /home/niels/course/2025-isceplus/miami/scripts/download_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml \
  --download --max-scenes 5
```

5. Download full selected stack (`20` scenes):
   Why: completes the raw input set for the next processing stage.

```bash
mamba run -n isce3-feb python /home/niels/course/2025-isceplus/miami/scripts/download_s1_stack.py \
  --repo-root /home/niels/course/2025-isceplus \
  --config miami/insar/us_isleofnormandy_s1_asc_t48/config/stack.toml \
  --download
```

## Next Inputs (Later Stage)
Why: coregistration will also require supporting geodata beyond raw SLC ZIPs.

- Precise orbit state vectors (POEORB)
- DEM over AOI

These are intentionally deferred until raw stack download is complete.
