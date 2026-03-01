# Dolphin Time-Series Stage (From COMPASS CSLC)

## Goal
Take COMPASS coregistered CSLC products and run phase linking, interferogram generation, unwrapping, and time-series inversion with Dolphin.

Canonical command reference: `miami/README.md` is the source of truth for runnable commands in this repo.

## Why This Stage Exists
COMPASS handles geocoding/coregistration and writes CSLC files per date/burst; Dolphin is the stage that turns those aligned CSLCs into InSAR displacement products.

## Official Alignment
- Dolphin CLI: `dolphin config` then `dolphin run`
- Workflow docs: https://dolphin-insar.readthedocs.io/en/latest/
- Repository: https://github.com/isce-framework/dolphin

## Inputs
- COMPASS outputs: `stack/compass/<burst_id>/<YYYYMMDD>/*.h5`
- Stack config: `miami/insar/us_isleofnormandy_s1_asc_t48/config/processing_configuration.toml`
- Subdataset to process inside CSLC HDF5 (default in this project: `data/VV`)

## Outputs
- `stack/dolphin/inputs/cslc_files.txt`: validated CSLC list
- `stack/dolphin/config/dolphin_config.yaml`: workflow config
- `stack/dolphin/prepare_summary.json`: prep summary
- `stack/dolphin/qc/ifg_network.png`: interferogram-network QC figure
- `stack/dolphin/qc/ifg_network_summary.json`: network metrics/edges summary
- `stack/dolphin/` run outputs from Dolphin (wrapped IFGs, unwrap, timeseries, velocity)

## Step 1: Prepare Dolphin Config
Why: validate CSLC completeness and generate a deterministic Dolphin config from project settings.

```bash
mamba run -n isce3-feb python scripts/09_prepare_dolphin_workflow.py \
  --repo-root . \
  --config <your_config.toml>
```

Notes:
- By default this fails if valid CSLC count is lower than `expected_unique_dates` from search config.
- Use `--allow-partial-cslc` only for testing on incomplete stacks.
- CSLC discovery is strict by default via `processing.dolphin.cslc_glob`; recursive `**/*.h5` fallback is opt-in (`allow_recursive_cslc_search=true`).
- QC figure generation is controlled by `[processing.dolphin.qc]`.

## Step 2: Run Dolphin Workflow
Why: execute wrapped phase estimation through time-series/velocity products.

```bash
mamba run -n isce3-feb python scripts/11_run_dolphin_workflow.py \
  --repo-root . \
  --config <your_config.toml>
```

For verbose troubleshooting:

```bash
mamba run -n isce3-feb python scripts/11_run_dolphin_workflow.py \
  --repo-root . \
  --config <your_config.toml> \
  --debug
```

Standalone QC re-run (without re-running prepare):

```bash
mamba run -n isce3-feb python scripts/10_plot_ifg_network_qc.py \
  --repo-root . \
  --config <your_config.toml>
```

## Minimal Practical Defaults
Current project defaults in `[processing.dolphin]`:
- `cslc_subdataset = "data/VV"`
- `ministack_size = 15`
- `max_bandwidth = 3`
- `run_unwrap = true`
- `gpu_enabled = false` (enable only when your installed stack supports GPU runtime reliably)
- `worker_block_shape = [128, 128]` and `timeseries_block_shape = [128, 128]` (safe RAM defaults)
- `reference_template_file = ".../dolphin_config.reference.yaml"` (auto-generated full Dolphin template)

## How To See All Dolphin Settings
Why: wrapper scripts expose the common high-value knobs, but Dolphin itself has many more.

1. Show every CLI option from your installed version:

```bash
mamba run -n isce3-feb dolphin config --help
```

2. Generate a full default YAML template (all sections/options):

```bash
mamba run -n isce3-feb dolphin config --print-empty --outfile /tmp/dolphin_empty.yaml
```

3. Project workflow shortcut:
- Running `09_prepare_dolphin_workflow.py` now also writes the same reference style file to:
  `miami/insar/us_isleofnormandy_s1_asc_t48/stack/dolphin/config/dolphin_config.reference.yaml`
- Use it as a catalog while keeping your actual run config deterministic.

4. Add unmapped flags without editing Python wrapper:
- Put raw Dolphin args in `processing.dolphin.extra_cli_args` (TOML list of strings).
- Preferred: use `processing.dolphin.option_overrides` for typed options:
  - bool -> `--option` / `--no-option`
  - scalar -> `--option value`
  - list -> `--option v1 v2 ...`
- The project TOML now carries a commented advanced option catalog under `option_overrides` so you can toggle most non-default knobs quickly.
- Do not duplicate wrapper-managed options in `option_overrides` or `extra_cli_args`; prepare fails fast on overlaps.
