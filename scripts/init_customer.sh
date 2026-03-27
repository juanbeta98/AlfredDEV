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
#   --osrm-data     Path to OSRM data directory to include  (optional)
#                   If provided, the docker-compose will reference it.
#                   Default: customer must supply OSRM data separately.
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
OSRM_DATA=""
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
    "${OUTPUT_DIR}/data/master" \
    "${OUTPUT_DIR}/data/runs" \
    "${OUTPUT_DIR}/app"

# ---------------------------------------------------------------------------
# .env.template
# ---------------------------------------------------------------------------
echo "[2/6] Copying master data..."
if [[ -d "${REPO_ROOT}/data/master" ]]; then
    cp -r "${REPO_ROOT}/data/master/." "${OUTPUT_DIR}/data/master/"
    echo "    Copied from ${REPO_ROOT}/data/master/"
else
    echo "    WARNING: ${REPO_ROOT}/data/master/ not found — copy it manually into data/master/"
fi

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
# REQUIRED: OSRM routing service (internal Docker service name)
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

# ----------------------------------------------------------
# DEV ONLY: Disable license validation (never set in production)
# ----------------------------------------------------------
# ALFRED_DEV_MODE=1
ENVTEMPLATE

# ---------------------------------------------------------------------------
# docker-compose.yml (Layer 1 — orchestrates alfred + osrm)
# ---------------------------------------------------------------------------
echo "[4/6] Writing docker-compose.yml..."

# Determine OSRM data volume line
if [[ -n "${OSRM_DATA}" ]]; then
    OSRM_VOLUME="      - ${OSRM_DATA}:/data:ro"
else
    OSRM_VOLUME="      - ./osrm_data:/data:ro  # Mount your OSRM data here"
fi

cat > "${OUTPUT_DIR}/docker-compose.yml" << COMPOSE
version: "3.9"

services:
  osrm:
    image: ghcr.io/project-osrm/osrm-backend:v5.27.1
    container_name: alfred-osrm
    restart: unless-stopped
    volumes:
${OSRM_VOLUME}
    command: osrm-routed --algorithm mld /data/colombia-latest.osrm
    networks:
      - alfred-net

  alfred:
    build: ./app
    container_name: alfred-solver
    restart: unless-stopped
    depends_on:
      - osrm
    env_file:
      - .env
    volumes:
      - ./license:/app/license:ro
      - ./data/master:/app/data/master:ro
      - ./data/runs:/app/data/runs
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
├── .env                       ← your credentials (copy from .env.template)
├── docker-compose.yml         ← orchestrates alfred + osrm
├── license/
│   └── alfred_license.json    ← your license file (provided by Alfred team)
├── data/                      ← solver output (created automatically)
├── app/                       ← app bundle (replaced on each update)
└── request/                   ← place your request.json here
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

   Unzip the app bundle (provided separately) into this directory:

   \`\`\`bash
   unzip <RELEASE_ID>.zip -d .
   # This creates/replaces the app/ directory
   \`\`\`

4. **Start the services**

   \`\`\`bash
   docker compose up --build -d
   \`\`\`

## Updating the app

When you receive a new app bundle zip, just replace \`app/\`:

\`\`\`bash
rm -rf app/
unzip <NEW_RELEASE_ID>.zip -d .
docker compose up --build -d
\`\`\`

Your \`.env\`, \`license/\`, and \`data/\` are never touched by updates.

## Running a request

Place your \`request.json\` in the \`request/\` directory, then:

\`\`\`bash
docker compose run --rm alfred
\`\`\`

Results are written to \`data/\`.
SETUPREADME

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
