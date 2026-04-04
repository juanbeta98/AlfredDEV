#!/usr/bin/env bash
# =============================================================================
# dev_deploy.sh — Build, package, and install a dev build in one command.
#
# Usage:
#   ./scripts/dev_deploy.sh [--platform mac|linux|all]
#
# Options:
#   --platform    Platform to build binary for (default: mac)
#   --help        Show this message.
#
# What it does:
#   1. Builds the alfred_solver binary via build_binary.sh
#   2. Creates a DEV package via deliver.sh (customer: Alfred / code: ALFRED)
#   3. Installs the package into the ALFRED_env via its install.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ALFRED_ENV="$(cd "${REPO_ROOT}/.." && pwd)/ALFRED_env"
PLATFORM="mac"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform) PLATFORM="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,17p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ ! -d "${ALFRED_ENV}" ]]; then
    echo "ERROR: ALFRED_env not found at ${ALFRED_ENV}" >&2
    exit 1
fi

if [[ ! -f "${ALFRED_ENV}/install.sh" ]]; then
    echo "ERROR: install.sh not found in ${ALFRED_ENV}" >&2
    exit 1
fi

echo "============================================================"
echo " dev_deploy.sh"
echo " Platform : ${PLATFORM}"
echo " Env      : ${ALFRED_ENV}"
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Build binary
# ---------------------------------------------------------------------------
echo "=== [1/3] Building binary ==="
bash "${SCRIPT_DIR}/build_binary.sh" --platform "${PLATFORM}"

# ---------------------------------------------------------------------------
# Step 2: Deliver (capture output to extract zip path)
# ---------------------------------------------------------------------------
echo ""
echo "=== [2/3] Creating DEV package ==="
DELIVER_OUTPUT="$(bash "${SCRIPT_DIR}/deliver.sh" \
    --type dev \
    --customer "Alfred" \
    --code ALFRED \
    --notes "dev build" \
    2>&1 | tee /dev/stderr)"

# Extract zip path from deliver.sh summary line: " Zip:         builds/DEV/..."
ZIP_REL="$(echo "${DELIVER_OUTPUT}" | grep -E '^\s+Zip:' | awk '{print $2}')"

if [[ -z "${ZIP_REL}" ]]; then
    echo "ERROR: Could not determine zip path from deliver.sh output." >&2
    exit 1
fi

# deliver.sh prints a relative path; resolve it against the repo root
if [[ "${ZIP_REL}" = /* ]]; then
    ZIP="${ZIP_REL}"
else
    ZIP="${REPO_ROOT}/${ZIP_REL}"
fi

if [[ ! -f "${ZIP}" ]]; then
    echo "ERROR: Expected zip not found: ${ZIP}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 3: Install into ALFRED_env
# ---------------------------------------------------------------------------
echo ""
echo "=== [3/3] Installing into ALFRED_env ==="
bash "${ALFRED_ENV}/install.sh" "${ZIP}"

echo ""
echo "============================================================"
echo " dev_deploy.sh done"
echo " Zip : ${ZIP}"
echo " Env : ${ALFRED_ENV}"
echo "============================================================"
