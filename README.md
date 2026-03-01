# 2025-isceplus
Course materials for the [2025 Technical Short Course: InSAR Processing and Analysis (ISCE+)](https://www.earthscope.org/event/insar-processing-and-analysis-isce/) plus reusable local InSAR workflows built around ISCE3, COMPASS, and Dolphin.

## Start Here (WSL2 + Linux)
Why: this repo is built around Linux-native geospatial tooling (`isce3`, `compass`, `dolphin`, GDAL).

1. Open a WSL2 Linux shell (Ubuntu) and work inside Linux paths.
2. Keep this repo under `/home/...` (recommended), not on `C:` / `/mnt/c/...`.
3. Open VS Code in WSL mode (`Remote - WSL`) if you use VS Code.

## Environment Setup
Why: all scripts assume the `isce3-feb` conda environment.

From repo root:

```bash
cd /home/niels/insar/git/2025-isceplus
mamba env update -n isce3-feb -f envs/isce3-feb.yml
# If mamba is not installed, use:
# conda env update -n isce3-feb -f envs/isce3-feb.yml
conda activate isce3-feb
```

Quick checks:

```bash
python -V
which python
python -m pip -V
```

Important:
- Do not run project scripts with `/bin/python3`.
- Use either `conda activate isce3-feb` + `python ...` or `mamba run -n isce3-feb python ...`.

## Credentials
Why: ASF (Earthdata) and OpenTopography calls need credentials before search/download/DEM steps.

```bash
bash scripts/00_check_credentials.sh
```

If checks fail, follow:
- `knowledge_library/01_credentials_earthdata_opentopography.md`

## Reusable Project Scaffold
Why: this repo now supports creating new AOI projects without editing Miami files.

Create a new project from template:

```bash
python scripts/01_create_project_from_example.py \
  --repo-root . \
  --project-name my_city
```

For decomposition workflows, create ASC+DSC stacks in one pass:

```bash
python scripts/01_create_project_from_example.py \
  --repo-root . \
  --project-name my_city \
  --dual-track
```

Then follow:
- `example_project/README.md` (full end-to-end instructions)
- `projects/my_city/.../config/processing_configuration.toml` (project-specific settings)

Useful setup helpers:
- `scripts/02_discover_s1_candidates.py`: discover candidate direction/orbit/frame geometry coverage before locking search settings.
- `scripts/04_suggest_reference_date.py`: suggest a baseline-aware reference date from your searched stack dates.
- `scripts/90_decompose_los_velocity.py`: decompose ASC/DSC LOS velocity rasters into East/Up velocity rasters.
- `scripts/10_plot_ifg_network_qc.py`: create interferogram-network QC PNG/JSON from prepared Dolphin inputs.

Config note:
- If your repo has multiple stack configs, always pass `--config <.../processing_configuration.toml>` explicitly.

## Repo Layout
- `course/`: course notebooks and lesson material (reference/tutorial content)
- `envs/`: conda environment files
- `miami/`: current Miami project + shared pipeline scripts
- `example_project/`: reusable project template
- `projects/`: generated/custom project workspaces
- `knowledge_library/`: step-by-step runbooks for recurring tasks
- `scripts/`: project bootstrap/discovery/credential helpers

## Next Docs
- Pipeline run order: `miami/README.md`
- Generic project workflow: `example_project/README.md`
- Project-specific status/details: `miami/insar/us_isleofnormandy_s1_asc_t48/README.md`
- Learning runbooks: `knowledge_library/README.md`
