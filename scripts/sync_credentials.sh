#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${repo_root}/.env"

if [[ ! -f "${env_file}" ]]; then
  echo "Missing ${env_file}. Copy .env.template to .env first."
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

earthdata_user="${EARTHDATA_USERNAME:-}"
earthdata_pass="${EARTHDATA_PASSWORD:-}"
opentopo_key="${OPENTOPOGRAPHY_API_KEY:-}"

if [[ -n "${earthdata_user}" && -n "${earthdata_pass}" ]]; then
  printf 'machine urs.earthdata.nasa.gov login %s password %s\n' \
    "${earthdata_user}" "${earthdata_pass}" > "${HOME}/.netrc"
  chmod 600 "${HOME}/.netrc"
  echo "Wrote ${HOME}/.netrc"
else
  echo "Skipped ~/.netrc (EARTHDATA_USERNAME/EARTHDATA_PASSWORD not both set)."
fi

if [[ -n "${opentopo_key}" ]]; then
  printf '%s\n' "${opentopo_key}" > "${HOME}/.topoapi"
  chmod 600 "${HOME}/.topoapi"
  echo "Wrote ${HOME}/.topoapi"
else
  echo "Skipped ~/.topoapi (OPENTOPOGRAPHY_API_KEY not set)."
fi
