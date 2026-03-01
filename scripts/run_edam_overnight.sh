#!/usr/bin/env bash
# Run full Edam/Volendam ASC+DSC pipeline overnight after bbox changes.
#
# How to run:
#   1) From repo root:
#        cd /home/niels/insar/git/2025-isceplus
#   2) Make executable once:
#        chmod +x scripts/run_edam_overnight.sh
#   3) Start in background with timestamped log:
#        LOG="projects/edam_volendam/logs/overnight_$(date +%Y%m%d_%H%M%S).log"
#        mkdir -p projects/edam_volendam/logs
#        nohup bash scripts/run_edam_overnight.sh > "$LOG" 2>&1 &
#        echo "PID=$! LOG=$LOG"
#   4) Monitor:
#        tail -f "$LOG"
#
# Notes:
#   - This script removes derived outputs (COMPASS/Dolphin/decomposition/search products)
#     for both stacks before rerunning.
#   - Raw SLC downloads under stack/slc are kept and reused.
#   - Decomposition is run once at the end to avoid duplicate runs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ENV_NAME="${ENV_NAME:-isce3-feb}"

ASC_CFG="${ASC_CFG:-projects/edam_volendam/insar/edam_volendam_s1_asc_t000/config/processing_configuration.toml}"
DSC_CFG="${DSC_CFG:-projects/edam_volendam/insar/edam_volendam_s1_dsc_t000/config/processing_configuration.toml}"

run() {
  echo "[$(date -Is)] $*"
  "$@"
}

abs_path() {
  local p="$1"
  if [[ "$p" = /* ]]; then
    printf "%s\n" "$p"
  else
    printf "%s/%s\n" "$REPO_ROOT" "$p"
  fi
}

stack_root_from_cfg() {
  local cfg="$1"
  dirname "$(dirname "$cfg")"
}

cd "$REPO_ROOT"

ASC_CFG_ABS="$(abs_path "$ASC_CFG")"
DSC_CFG_ABS="$(abs_path "$DSC_CFG")"

if [[ ! -f "$ASC_CFG_ABS" ]]; then
  echo "Missing ASC config: $ASC_CFG_ABS" >&2
  exit 2
fi
if [[ ! -f "$DSC_CFG_ABS" ]]; then
  echo "Missing DSC config: $DSC_CFG_ABS" >&2
  exit 2
fi

echo "Repo root: $REPO_ROOT"
echo "Conda env: $ENV_NAME"
echo "ASC config: $ASC_CFG_ABS"
echo "DSC config: $DSC_CFG_ABS"

for CFG in "$ASC_CFG_ABS" "$DSC_CFG_ABS"; do
  ROOT="$(stack_root_from_cfg "$CFG")"
  echo "Cleaning derived outputs under: $ROOT"
  rm -rf \
    "$ROOT/stack/compass" \
    "$ROOT/stack/dolphin" \
    "$ROOT/stack/decomposition" \
    "$ROOT/logs/compass" \
    "$ROOT/search/products"
done

for CFG in "$ASC_CFG_ABS" "$DSC_CFG_ABS"; do
  run mamba run -n "$ENV_NAME" python scripts/03_search_s1_stack.py --repo-root . --config "$CFG"
  run mamba run -n "$ENV_NAME" python scripts/05_download_s1_stack.py --repo-root . --config "$CFG" --download
  run mamba run -n "$ENV_NAME" python scripts/06_download_dem_opentopography.py --repo-root . --config "$CFG" --overwrite
  run mamba run -n "$ENV_NAME" python scripts/07_prepare_compass_stack.py --repo-root . --config "$CFG"
  run mamba run -n "$ENV_NAME" python scripts/08_run_compass_runfiles.py --repo-root . --config "$CFG" --no-resume
  run mamba run -n "$ENV_NAME" python scripts/09_prepare_dolphin_workflow.py --repo-root . --config "$CFG"
  run mamba run -n "$ENV_NAME" python scripts/11_run_dolphin_workflow.py --repo-root . --config "$CFG" --skip-decomposition
done

# Run decomposition once after both stacks finish.
run mamba run -n "$ENV_NAME" python scripts/90_decompose_los_velocity.py --repo-root . --config "$ASC_CFG_ABS"

echo "[$(date -Is)] Overnight pipeline completed successfully."
