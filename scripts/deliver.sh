#!/usr/bin/env bash
# deliver.sh — Generate an AlfredProd delivery package (DEV or PROD).
#
# Usage:
#   ./scripts/deliver.sh --type prod --customer "Cliente S.A." --code CLIENTE \
#                        --expires 2026-12-31 [--notes "Initial delivery"]
#                        [--binary dist/alfred_solver] [--skip-binary]
#
#   ./scripts/deliver.sh --type dev  --customer "Cliente S.A." --code CLIENTE \
#                        [--notes "QA build"] [--binary dist/alfred_solver] [--skip-binary]
#
# Options:
#   --type          prod | dev   (required)
#   --customer      Customer name for license payload (required)
#   --code          Short uppercase code for release ID, e.g. CLIENTE (required)
#   --expires       License expiry date YYYY-MM-DD (required for prod, ignored for dev)
#   --notes         Free-text notes appended to log.csv (optional)
#   --binary        Path to compiled alfred_solver binary (default: dist/alfred_solver)
#   --skip-binary   Omit binary from package (passed through to build_prod.sh)
#
# Output:
#   releases/zips/<release_id>.zip   — deliverable archive
#   releases/licenses/<id>.json      — signed license (PROD only, committed to git)
#   releases/log.csv                 — one new row appended

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve repo root (script may be called from any working directory)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
BUILD_TYPE=""
CUSTOMER=""
CODE=""
EXPIRES=""
NOTES=""
BINARY="${REPO_ROOT}/dist/alfred_solver"
SKIP_BINARY=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
usage() {
    grep '^#' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --type)       BUILD_TYPE="$2";   shift 2 ;;
        --customer)   CUSTOMER="$2";     shift 2 ;;
        --code)       CODE="$2";         shift 2 ;;
        --expires)    EXPIRES="$2";      shift 2 ;;
        --notes)      NOTES="$2";        shift 2 ;;
        --binary)     BINARY="$2";       shift 2 ;;
        --skip-binary) SKIP_BINARY=1;    shift   ;;
        --help|-h)    usage ;;
        *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate required args
# ---------------------------------------------------------------------------
if [[ -z "$BUILD_TYPE" ]]; then
    echo "ERROR: --type is required (prod or dev)" >&2; exit 1
fi
if [[ "$BUILD_TYPE" != "prod" && "$BUILD_TYPE" != "dev" ]]; then
    echo "ERROR: --type must be 'prod' or 'dev'" >&2; exit 1
fi
if [[ -z "$CUSTOMER" ]]; then
    echo "ERROR: --customer is required" >&2; exit 1
fi
if [[ -z "$CODE" ]]; then
    echo "ERROR: --code is required" >&2; exit 1
fi
if [[ "$BUILD_TYPE" == "prod" && -z "$EXPIRES" ]]; then
    echo "ERROR: --expires is required for prod builds" >&2; exit 1
fi

# Force CODE to uppercase
CODE="${CODE^^}"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
RELEASES_DIR="${REPO_ROOT}/releases"
LICENSES_DIR="${RELEASES_DIR}/licenses"
ZIPS_DIR="${RELEASES_DIR}/zips"
LOG_CSV="${RELEASES_DIR}/log.csv"
BUILD_TMP="${RELEASES_DIR}/build_tmp"

mkdir -p "${LICENSES_DIR}" "${ZIPS_DIR}"

# ---------------------------------------------------------------------------
# Step 1: Determine release ID
# ---------------------------------------------------------------------------
ISSUED_AT="$(date +%Y-%m-%d)"

if [[ "$BUILD_TYPE" == "prod" ]]; then
    # Find the highest R<N> number in log.csv (PROD rows only)
    if [[ -f "$LOG_CSV" ]]; then
        LAST_N=$(grep "^PROD," "$LOG_CSV" 2>/dev/null \
            | awk -F',' '{print $2}' \
            | grep -oE '^R[0-9]+' \
            | grep -oE '[0-9]+' \
            | sort -n \
            | tail -1)
    else
        LAST_N=""
    fi
    NEXT_N=$(( ${LAST_N:-0} + 1 ))
    RELEASE_ID="R${NEXT_N}_${CODE}"
else
    # DEV: use datetime
    DATETIME="$(date +%Y%m%d_%H%M)"
    RELEASE_ID="RDEV_${CODE}_${DATETIME}"
fi

echo "==> Release ID: ${RELEASE_ID}"

# ---------------------------------------------------------------------------
# Step 2: Build AlfredProd
# ---------------------------------------------------------------------------
echo "==> Building AlfredProd..."
rm -rf "${BUILD_TMP}"

BUILD_ARGS=("--output-dir" "${BUILD_TMP}")
if [[ $SKIP_BINARY -eq 1 ]]; then
    BUILD_ARGS+=("--skip-binary")
else
    BUILD_ARGS+=("--binary" "${BINARY}")
fi

bash "${SCRIPT_DIR}/build_prod.sh" "${BUILD_ARGS[@]}"

# ---------------------------------------------------------------------------
# Step 3: License handling
# ---------------------------------------------------------------------------
if [[ "$BUILD_TYPE" == "prod" ]]; then
    LICENSE_OUT="${LICENSES_DIR}/${RELEASE_ID}.json"
    echo "==> Issuing license → ${LICENSE_OUT}"
    python "${REPO_ROOT}/license_tools/issue_license.py" \
        --customer "${CUSTOMER}" \
        --expires "${EXPIRES}" \
        --out "${LICENSE_OUT}"

    echo "==> Placing license in build..."
    cp "${LICENSE_OUT}" "${BUILD_TMP}/license/alfred_license.json"

    # Safety check: PROD build must NOT have ALFRED_DEV_MODE set
    if [[ -f "${BUILD_TMP}/.env" ]] && grep -q "ALFRED_DEV_MODE" "${BUILD_TMP}/.env"; then
        echo "ERROR: ALFRED_DEV_MODE found in PROD build .env — aborting." >&2
        rm -rf "${BUILD_TMP}"
        exit 1
    fi
else
    echo "==> DEV build — injecting ALFRED_DEV_MODE=1 into .env..."
    echo "" >> "${BUILD_TMP}/.env"
    echo "# DEV MODE — license check disabled" >> "${BUILD_TMP}/.env"
    echo "ALFRED_DEV_MODE=1" >> "${BUILD_TMP}/.env"
fi

# ---------------------------------------------------------------------------
# Step 4: Compute binary hash
# ---------------------------------------------------------------------------
BINARY_HASH=""
if [[ $SKIP_BINARY -eq 0 && -f "${BUILD_TMP}/bin/alfred_solver" ]]; then
    echo "==> Computing binary hash..."
    BINARY_HASH="$(shasum -a 256 "${BUILD_TMP}/bin/alfred_solver" | awk '{print $1}')"
    echo "    sha256: ${BINARY_HASH}"
fi

# ---------------------------------------------------------------------------
# Step 5: Zip
# ---------------------------------------------------------------------------
ZIP_PATH="${ZIPS_DIR}/${RELEASE_ID}.zip"
echo "==> Creating zip → ${ZIP_PATH}"
(cd "${RELEASES_DIR}" && zip -r "${ZIP_PATH}" "build_tmp" -x "build_tmp/.git/*")
# Rename the top-level folder inside the zip to the release ID
# (zip was created as build_tmp/; repack with correct name)
TMP_REPACK="${RELEASES_DIR}/_repack_tmp"
mkdir -p "${TMP_REPACK}"
mv "${BUILD_TMP}" "${TMP_REPACK}/${RELEASE_ID}"
rm -f "${ZIP_PATH}"
(cd "${TMP_REPACK}" && zip -r "${ZIP_PATH}" "${RELEASE_ID}")
rm -rf "${TMP_REPACK}"

# ---------------------------------------------------------------------------
# Step 6: Append to log.csv
# ---------------------------------------------------------------------------
echo "==> Logging delivery..."
if [[ "$BUILD_TYPE" == "prod" ]]; then
    LOG_EXPIRES="${EXPIRES}"
else
    LOG_EXPIRES=""
fi

# Escape any commas in NOTES
NOTES_SAFE="${NOTES//,/;}"

echo "${BUILD_TYPE^^},${RELEASE_ID},${CUSTOMER},${ISSUED_AT},${LOG_EXPIRES},${BINARY_HASH},${NOTES_SAFE}" >> "${LOG_CSV}"

# ---------------------------------------------------------------------------
# Step 7: Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Delivery complete: ${RELEASE_ID}"
echo "------------------------------------------------------------"
echo " Type:        ${BUILD_TYPE^^}"
echo " Customer:    ${CUSTOMER}"
echo " Issued:      ${ISSUED_AT}"
if [[ "$BUILD_TYPE" == "prod" ]]; then
echo " Expires:     ${EXPIRES}"
echo " License:     ${LICENSE_OUT}"
fi
echo " Zip:         ${ZIP_PATH}"
if [[ -n "$BINARY_HASH" ]]; then
echo " Binary SHA:  ${BINARY_HASH}"
fi
echo "============================================================"
