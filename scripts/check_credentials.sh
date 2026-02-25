#!/usr/bin/env bash
set -euo pipefail

netrc_path="${HOME}/.netrc"
topoapi_path="${HOME}/.topoapi"

check_mode_600() {
  local file="$1"
  if command -v stat >/dev/null 2>&1; then
    local mode
    mode="$(stat -c '%a' "$file")"
    [[ "$mode" == "600" ]]
  else
    return 0
  fi
}

echo "Checking Earthdata/OpenTopography credential files..."
echo

if [[ -f "${netrc_path}" ]]; then
  echo "[OK] Found ${netrc_path}"
  if grep -q "machine urs.earthdata.nasa.gov" "${netrc_path}"; then
    echo "[OK] Earthdata entry exists in .netrc"
  else
    echo "[WARN] .netrc exists but has no 'machine urs.earthdata.nasa.gov' entry"
  fi
  if check_mode_600 "${netrc_path}"; then
    echo "[OK] .netrc permissions are 600"
  else
    echo "[WARN] .netrc permissions are not 600 (recommended: chmod 600 ~/.netrc)"
  fi
else
  echo "[WARN] Missing ${netrc_path}"
  echo "       Create it with:"
  echo "       echo \"machine urs.earthdata.nasa.gov login <username> password <password>\" > ~/.netrc"
  echo "       chmod 600 ~/.netrc"
fi

echo

if [[ -f "${topoapi_path}" ]]; then
  echo "[OK] Found ${topoapi_path}"
  if [[ -s "${topoapi_path}" ]]; then
    echo "[OK] .topoapi is non-empty"
  else
    echo "[WARN] .topoapi exists but is empty"
  fi
  if check_mode_600 "${topoapi_path}"; then
    echo "[OK] .topoapi permissions are 600"
  else
    echo "[WARN] .topoapi permissions are not 600 (recommended: chmod 600 ~/.topoapi)"
  fi
else
  echo "[WARN] Missing ${topoapi_path}"
  echo "       Create it with:"
  echo "       echo \"<OpenTopography_API_Key>\" > ~/.topoapi"
  echo "       chmod 600 ~/.topoapi"
fi
