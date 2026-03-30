#!/usr/bin/env bash
# =============================================================================
# init_customer.sh — Generate the one-time Layer 1 installation root package.
#
# Usage:
#   ./scripts/init_customer.sh --customer "Cliente S.A." --code CLIENTE \
#                              [--osrm-data <path>] [--output-dir <path>]
#
# Options:
#   --customer      Customer name (used in README)          (required)
#   --code          Short uppercase code, e.g. CLIENTE      (required)
#   --osrm-data     Path to pre-processed OSRM data dir     (optional)
#                   The directory is copied into the env as osrm_data/.
#                   Default: osrm_resources/osrm_data/colombia (repo default)
#   --output-dir    Override output directory               (optional)
#                   Default: releases/envs/<CODE>_env/
#   --help          Show this message.
#
# Output:
#   A directory (and zip) containing the customer's permanent installation root:
#
#     <CODE>_env/
#     ├── .env.template        ← customer renames to .env and fills in values
#     ├── docker-compose.yml   ← orchestrates alfred + osrm services
#     ├── osrm_data/           ← pre-processed Colombia routing data (bundled)
#     ├── license/
#     │   └── README.md        ← instructions for placing alfred_license.json
#     ├── data/                ← output directory (created empty)
#     ├── app/                 ← placeholder dir for the app bundle (Layer 2)
#     │   └── README.md
#     └── README.md            ← customer setup guide
#
# This package is delivered ONCE per new customer. Subsequent app updates are
# delivered as app bundle zips (via deliver.sh) that replace only app/.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
CUSTOMER=""
CODE=""
OSRM_DATA="${REPO_ROOT}/osrm_resources/osrm_data/colombia"
OUTPUT_DIR=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --customer)   CUSTOMER="$2";   shift 2 ;;
        --code)       CODE="$2";       shift 2 ;;
        --osrm-data)  OSRM_DATA="$2";  shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,40p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
if [[ -z "$CUSTOMER" ]]; then
    echo "ERROR: --customer is required" >&2; exit 1
fi
if [[ -z "$CODE" ]]; then
    echo "ERROR: --code is required" >&2; exit 1
fi

CODE="$(echo "$CODE" | tr '[:lower:]' '[:upper:]')"

if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="${REPO_ROOT}/releases/envs/${CODE}_env"
fi

echo "=== init_customer ==="
echo "Customer : ${CUSTOMER}"
echo "Code     : ${CODE}"
echo "Output   : ${OUTPUT_DIR}"
echo ""

# ---------------------------------------------------------------------------
# Create directory structure
# ---------------------------------------------------------------------------
echo "[1/6] Creating directory structure..."
rm -rf "${OUTPUT_DIR}"
mkdir -p \
    "${OUTPUT_DIR}/license" \
    "${OUTPUT_DIR}/runs" \
    "${OUTPUT_DIR}/request" \
    "${OUTPUT_DIR}/builds" \
    "${OUTPUT_DIR}/app"

# ---------------------------------------------------------------------------
# OSRM data
# ---------------------------------------------------------------------------
echo "[2/6] Copying OSRM data from ${OSRM_DATA}..."
if [[ ! -d "${OSRM_DATA}" ]]; then
    echo "ERROR: OSRM data directory not found: ${OSRM_DATA}" >&2
    echo "  Provide a valid path with --osrm-data <path>" >&2
    exit 1
fi
cp -r "${OSRM_DATA}" "${OUTPUT_DIR}/osrm_data"

# ---------------------------------------------------------------------------
# .env.template
# ---------------------------------------------------------------------------
# Master data is baked into the Docker image via the app bundle (data/master/).
# No copy needed in Layer 1 for Docker runs. If the customer runs natively,
# they can find master data inside app/data/master/ after installing the bundle.

echo "[3/6] Writing .env.template..."
cat > "${OUTPUT_DIR}/.env.template" << 'ENVTEMPLATE'
# ============================================================
# Alfred — Environment Configuration
# Copy this file to .env and fill in the required values.
# ============================================================

# ----------------------------------------------------------
# REQUIRED: API mode (must be true for production)
# ----------------------------------------------------------
USE_API=true

# ----------------------------------------------------------
# REQUIRED: API credentials
# ----------------------------------------------------------
API_BASE_URL=https://<host>
API_TOKEN=<your-api-token>

# ----------------------------------------------------------
# REQUIRED: License file path (relative to this directory)
# ----------------------------------------------------------
ALFRED_LICENSE=./license/alfred_license.json

# ----------------------------------------------------------
# REQUIRED: OSRM routing service URL
#   Docker (docker-compose with osrm profile): http://osrm:5000/route/v1/driving/
#   Native (OSRM running locally on port 5050): http://localhost:5050/route/v1/driving/
# ----------------------------------------------------------
OSRM_URL=http://osrm:5000/route/v1/driving/

# ----------------------------------------------------------
# OPTIONAL: Logging
# ----------------------------------------------------------
# LOG_LEVEL=INFO

# ----------------------------------------------------------
# OPTIONAL: Solver timeouts (seconds)
# ----------------------------------------------------------
# ALFRED_SOLVER_TIMEOUT=600
# ALFRED_PROBE_TIMEOUT=60
ENVTEMPLATE

# ---------------------------------------------------------------------------
# docker-compose.yml (Layer 1 — orchestrates alfred + osrm)
# ---------------------------------------------------------------------------
echo "[4/6] Writing docker-compose.yml..."

cat > "${OUTPUT_DIR}/docker-compose.yml" << COMPOSE
version: "3.9"

# How to run:
#   With OSRM (full stack):  docker-compose --profile osrm up -d osrm
#                            docker-compose run --rm alfred
#   Without OSRM:            docker-compose run --rm alfred
#
# Note: set OSRM_URL=http://osrm:5000/route/v1/driving/ in .env when using the
#       osrm service. For native (non-Docker) runs use http://localhost:<port>/...

services:
  osrm:
    profiles: ["osrm"]
    image: ghcr.io/project-osrm/osrm-backend:v5.27.1
    container_name: alfred-osrm
    restart: unless-stopped
    volumes:
      - ./osrm_data:/data:ro
    command: osrm-routed --algorithm mld /data/colombia-latest.osrm
    networks:
      - alfred-net

  alfred:
    build: ./app
    platform: linux/amd64
    container_name: alfred-solver
    restart: unless-stopped
    env_file:
      - .env
    ports:
      - "8000:8000"
    volumes:
      - ./license:/app/license:ro
      - ./runs:/app/data/runs
      - ./request:/app/request
    networks:
      - alfred-net

networks:
  alfred-net:
    driver: bridge
COMPOSE

# ---------------------------------------------------------------------------
# license/README.md
# ---------------------------------------------------------------------------
echo "[5/6] Writing license/README.md..."
cat > "${OUTPUT_DIR}/license/README.md" << 'LICREADME'
# license/

Place the license file issued for this deployment here:

    license/alfred_license.json

The file is referenced via the ALFRED_LICENSE variable in .env:

    ALFRED_LICENSE=./license/alfred_license.json

The solver binary verifies the license on every invocation. It will exit
with code 5 if the file is missing, tampered with, or expired.

License files are issued by the Alfred team. Contact them to obtain or
renew a license. A new license is only needed when:
  - Your current license expires
  - A new keypair is issued (rare; announced by the Alfred team)

App bundle updates (new app/ zips) do NOT require a new license.
LICREADME

# ---------------------------------------------------------------------------
# alfred_cli.py launcher stub (Layer 1 entry point)
# ---------------------------------------------------------------------------
echo "[2b/6] Writing alfred_cli.py launcher stub..."
cat > "${OUTPUT_DIR}/alfred_cli.py" << 'CLISTUB'
#!/usr/bin/env python3
"""ALFRED launcher — delegates to the current app bundle.

Run from this directory:
    python alfred_cli.py --request request/request.json
"""
import os
import sys
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.exit(subprocess.call(
    [sys.executable, os.path.join(_HERE, "app", "alfred_cli.py")] + sys.argv[1:]
))
CLISTUB
chmod +x "${OUTPUT_DIR}/alfred_cli.py"

# ---------------------------------------------------------------------------
# app/README.md placeholder
# ---------------------------------------------------------------------------
cat > "${OUTPUT_DIR}/app/README.md" << 'APPREADME'
# app/

This directory holds the Alfred app bundle (Layer 2).

To install or update the app, unzip the latest app bundle here:

    unzip <RELEASE_ID>.zip -d .

This will replace the contents of app/ with the new version.
Your .env, license/, and data/ are untouched.
APPREADME

# ---------------------------------------------------------------------------
# Top-level README
# ---------------------------------------------------------------------------
cat > "${OUTPUT_DIR}/README.md" << SETUPREADME
# Alfred — Installation Guide for ${CUSTOMER}

## Directory structure

\`\`\`
alfred/                        ← this directory (your permanent installation root)
├── alfred_cli.py              ← run this to invoke Alfred
├── .env                       ← your credentials (copy from .env.template)
├── docker-compose.yml         ← orchestrates alfred + osrm
├── install.sh                 ← run this to install/update the app bundle
├── license/
│   └── alfred_license.json    ← your license file (provided by Alfred team)
├── runs/                      ← solver output artifacts (created automatically)
├── request/                   ← place your request.json here
└── app/  (or app -> builds/<id>/)  ← app bundle (managed by install.sh)
\`\`\`

## First-time setup

1. **Configure credentials**

   \`\`\`bash
   cp .env.template .env
   # Edit .env and fill in API_BASE_URL and API_TOKEN
   \`\`\`

2. **Place your license file**

   Copy the \`alfred_license.json\` provided by the Alfred team into \`license/\`:

   \`\`\`bash
   cp alfred_license.json license/alfred_license.json
   \`\`\`

3. **Install the app bundle**

   Run the install script with the app bundle zip (provided separately):

   \`\`\`bash
   ./install.sh <RELEASE_ID>.zip
   \`\`\`

4. **Build and run**

   \`\`\`bash
   docker compose build alfred
   docker compose run --rm alfred
   \`\`\`

## Updating the app

When you receive a new app bundle zip, just run:

\`\`\`bash
./install.sh <NEW_RELEASE_ID>.zip
docker compose build alfred
\`\`\`

Previous builds are kept in \`builds/\` for rollback. Your \`.env\`, \`license/\`, and \`runs/\` are never touched by updates.

## Running a request

Place your \`request.json\` in the \`request/\` directory, then run:

\`\`\`bash
python alfred_cli.py --request request/request.json
\`\`\`

Or via Docker:

\`\`\`bash
docker compose run --rm alfred
\`\`\`

Results are written to \`runs/\`.
SETUPREADME

# ---------------------------------------------------------------------------
# install.sh
# ---------------------------------------------------------------------------
cp "${SCRIPT_DIR}/install.sh.template" "${OUTPUT_DIR}/install.sh"
chmod +x "${OUTPUT_DIR}/install.sh"

# ---------------------------------------------------------------------------
# Zip it
# ---------------------------------------------------------------------------
echo "[6/6] Creating zip..."
INIT_ZIP="${REPO_ROOT}/releases/envs/${CODE}_env.zip"
mkdir -p "${REPO_ROOT}/releases/envs"
PARENT_DIR="$(dirname "${OUTPUT_DIR}")"
FOLDER_NAME="$(basename "${OUTPUT_DIR}")"
(cd "${PARENT_DIR}" && zip -r "${INIT_ZIP}" "${FOLDER_NAME}")

echo ""
echo "=== init_customer complete ==="
echo "Setup package : ${OUTPUT_DIR}/"
echo "Zip           : ${INIT_ZIP}"
echo ""
echo "Deliver this zip ONCE to the customer."
echo "Subsequent app updates use: ./scripts/deliver.sh --type prod ..."
