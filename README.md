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
bash scripts/check_credentials.sh
```

If checks fail, follow:
- `knowledge_library/01_credentials_earthdata_opentopography.md`

## Reusable Project Scaffold
Why: this repo now supports creating new AOI projects without editing Miami files.

Create a new project from template:

```bash
python scripts/create_project_from_example.py \
  --repo-root . \
  --project-name my_city
```

Then follow:
- `example_project/README.md` (full end-to-end instructions)
- `projects/my_city/.../config/processing_configuration.toml` (project-specific settings)

Useful setup helpers:
- `scripts/discover_s1_candidates.py`: discover candidate direction/orbit/frame geometry coverage before locking search settings.
- `scripts/suggest_reference_date.py`: suggest a reference date from your searched stack dates.

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
