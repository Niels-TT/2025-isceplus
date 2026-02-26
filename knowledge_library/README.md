# Knowledge Library

Personal runbooks for repeatable InSAR setup and processing tasks.

## Prerequisites For These Runbooks
Why: command examples assume Linux/WSL2 and the same conda environment.

- Open this repo in WSL2/Linux (`/home/...` path recommended).
- Use the `isce3-feb` environment:

```bash
mamba env update -n isce3-feb -f envs/isce3-feb.yml
# If mamba is not installed, use:
# conda env update -n isce3-feb -f envs/isce3-feb.yml
conda activate isce3-feb
```

- Verify interpreter:

```bash
which python
python -m pip -V
```

- Avoid `/bin/python3`; it bypasses your conda environment.

## Recommended Reading Order
1. [01_credentials_earthdata_opentopography.md](./01_credentials_earthdata_opentopography.md)
2. [02_sentinel1_stack_search.md](./02_sentinel1_stack_search.md)
3. [03_post_download_preprocessing_compass.md](./03_post_download_preprocessing_compass.md)
4. [04_dolphin_timeseries_from_compass_cslc.md](./04_dolphin_timeseries_from_compass_cslc.md)
5. [05_new_project_setup_and_decisions.md](./05_new_project_setup_and_decisions.md)
