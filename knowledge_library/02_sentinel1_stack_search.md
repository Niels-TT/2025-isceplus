# Sentinel-1 Stack Search Playbook (ASF + Local Script)

## Goal
Define a reproducible AOI/date/orbit query and generate a verified scene list before any large download.

## Why This Matters
Search-first prevents downloading the wrong stack and avoids accidental storage overload.

## Inputs You Need
- AOI polygon (KML)
- Date range
- Orbit direction (`ASCENDING` or `DESCENDING`)
- Relative orbit/track number
- Product type (`SLC`)
- Beam mode (`IW`)
- Optional reference date

## Geometry Discovery (Script + Vertex)
Why: choosing direction/orbit/frame from coverage statistics is more reliable than guessing.

Script-assisted discovery:

```bash
mamba run -n isce3-feb python scripts/discover_s1_candidates.py \
  --repo-root . \
  --config <your_config.toml>
```

Then optionally validate in ASF Vertex.

## Discovery in ASF Vertex (Manual Recon)
Why: Vertex is the fastest way to explore what exists before hard-coding parameters.

1. Open <https://search.asf.alaska.edu/>
2. Draw/import AOI and set date range.
3. Set platform: Sentinel-1.
4. Set product type: SLC.
5. Filter by direction and relative orbit.
6. Inspect counts and scene spacing.
7. Copy final constraints into stack config (`processing_configuration.toml`).

Use manual recon when:
- you do not know orbit direction/track yet
- coverage is sparse
- you need confidence in scene availability

## Local Project Layout (Generic)
Why: standard structure keeps automation and debugging predictable across AOIs.

- `<project_root>/aux/bbox.kml` (source AOI)
- `<project_root>/insar/<stack_name>/config/processing_configuration.toml`
- `<project_root>/insar/<stack_name>/search/`
  - `products/scene_names.txt`
  - `products/scenes.csv`
  - `products/summary.json`
  - `raw/results.geojson`
  - `raw/aoi.wkt`

## Run Stack Search
Why: Generate machine-usable scene lists with explicit reproducibility.

```bash
mamba run -n isce3-feb python miami/scripts/search_s1_stack.py \
  --repo-root . \
  --config <your_config.toml>
```

What the script does:
- reads `processing_configuration.toml`
- converts KML AOI to WKT
- queries ASF (`asf_search`)
- writes scene names, CSV metadata, GeoJSON, and summary
- validates scene/date counts vs expected

## Verify Results
Why: Count checks catch parameter drift before expensive downstream work.

Check:
- `selected_scene_count` and `selected_unique_date_count` in `summary.json`
- first/last date
- reference date is present in `scene_names.txt`

Example (Miami stack):
- expected selected `20/20`
- found selected `20/20` (from full `161/161`)
- selected span `2015-09-21` to `2017-03-26`

Optional helper after search:

```bash
mamba run -n isce3-feb python scripts/suggest_reference_date.py \
  --repo-root . \
  --config <your_config.toml>
```

## Storage Planning Before Download
Why: Sentinel-1 SLC stacks are large; full-stack pull can exceed local free space.

For current selected stack (`20` scenes):
- total size from ASF metadata: about `92.36 GB` decimal
- average scene size: about `4.62 GB`

For full stack context (`161` scenes):
- total size from ASF metadata: about `681 GB` decimal (`634 GiB`)
- average scene size: about `4.23 GB`

Decision rule:
- if free space is below estimated total + working overhead, do not bulk-download
- prefer staged download/processing or external storage

## Why Search Does Not Download
Why: Separation of search and download is deliberate safety and reproducibility design.

Search stage only creates metadata and scene manifests.
Download stage should be explicit and separately scripted.

## Next Stage (After Search)
Why: Controlled progression reduces risk and preserves traceability.

1. Download SLCs and precise orbits in batches.
2. Build stack directory under `stack/`.
3. Run coregistration workflow.
4. Run Dolphin time-series workflow.

For the concrete post-download workflow, see:
- `knowledge_library/03_post_download_preprocessing_compass.md`

## Download Step (Current Miami Workflow)
Why: keep download logic explicit and storage-safe instead of auto-pulling by accident.

Dry-run:

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root . \
  --config <your_config.toml>
```

Execute:

```bash
mamba run -n isce3-feb python miami/scripts/download_s1_stack.py \
  --repo-root . \
  --config <your_config.toml> \
  --download
```

Notes:
- downloader reads selected scenes from `search/products/scenes.csv`
- uses Earthdata credentials from `~/.netrc`
- writes raw SLC ZIPs to `stack/slc/`
- writes status to `stack/download_manifest.json`
