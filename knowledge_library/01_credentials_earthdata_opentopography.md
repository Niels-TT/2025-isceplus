# Earthdata + OpenTopography Credentials (`.netrc` and `.topoapi`)

## Goal
Store credentials once at the OS user level so ASF/ARIA tooling can authenticate non-interactively.

## Why This Matters
Automation breaks if credentials are only in browser sessions; command-line tools need local files.

## What Files Are Used
- `~/.netrc`: Earthdata username/password (used by ASF/ARIA workflows)
- `~/.topoapi`: OpenTopography API key (used by DEM download tooling)

## Step 1: Create `~/.netrc`
Why: Earthdata auth is required for most ASF-protected downloads.

```bash
read -p "Earthdata username: " ED_USER
read -s -p "Earthdata password: " ED_PASS; echo
printf "machine urs.earthdata.nasa.gov login %s password %s\n" "$ED_USER" "$ED_PASS" > ~/.netrc
chmod 600 ~/.netrc
unset ED_USER ED_PASS
```

## Step 2: Create `~/.topoapi`
Why: OpenTopography API access is required by multiple InSAR/ARIA DEM workflows.

```bash
read -p "OpenTopography API key: " OT_KEY
printf "%s\n" "$OT_KEY" > ~/.topoapi
chmod 600 ~/.topoapi
unset OT_KEY
```

## Step 3: Validate Both Files
Why: Early validation avoids long pipeline runs failing on authentication.

```bash
/home/niels/course/2025-isceplus/scripts/00_check_credentials.sh
```

Expected signal:
- `.netrc` exists, has `machine urs.earthdata.nasa.gov`, permissions `600`
- `.topoapi` exists, non-empty, permissions `600`

## Security Notes
Why: These files contain secrets and should not leak into Git history.

- Never commit `~/.netrc` or `~/.topoapi`
- Keep permissions at `600`
- If compromised, rotate Earthdata password and regenerate OpenTopography key

## Troubleshooting
Why: Most failures are simple formatting or permission issues.

- `Username/Password Authentication Failed`:
  - verify `.netrc` machine line and credentials
- tool ignores `.netrc`:
  - ensure `chmod 600 ~/.netrc`
- OpenTopography says API key missing:
  - ensure `.topoapi` is non-empty and key is valid
