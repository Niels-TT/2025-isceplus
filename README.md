# 2025-isceplus
Course materials for the [2025 Technical Short Course: InSAR Processing and Analysis (ISCE+)](https://www.earthscope.org/event/insar-processing-and-analysis-isce/) plus a local end-to-end Miami InSAR workflow.

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

## Repo Layout
- `course/`: course notebooks and lesson material (reference/tutorial content)
- `envs/`: conda environment files
- `miami/`: active project pipeline and scripts
- `knowledge_library/`: step-by-step runbooks for recurring tasks
- `scripts/check_credentials.sh`: credential validation helper

## Next Docs
- Pipeline run order: `miami/README.md`
- Project-specific status/details: `miami/insar/us_isleofnormandy_s1_asc_t48/README.md`
- Learning runbooks: `knowledge_library/README.md`
