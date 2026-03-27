#!/usr/bin/env bash
# deliver.sh — Generate an AlfredProd delivery package (DEV or PROD).
#
# Usage:
#   ./scripts/deliver.sh --type prod --customer "Cliente S.A." --code CLIENTE \
#                        --expires 2026-12-31 [--notes "Initial delivery"]
#                        [--binary-mac dist/alfred_solver_mac] [--binary-linux dist/alfred_solver_linux]
#                        [--skip-binary] [--skip-linux] [--build-linux]
#
#   ./scripts/deliver.sh --type dev  --customer "Cliente S.A." --code CLIENTE \
#                        [--notes "QA build"] [--binary-mac dist/alfred_solver_mac] [--skip-binary]
#
# Options:
#   --type          prod | dev   (required)
#   --customer      Customer name for license payload (required)
#   --code          Short uppercase code for release ID, e.g. CLIENTE (required)
#   --expires       License expiry date YYYY-MM-DD (required for prod, ignored for dev)
#   --notes         Free-text notes appended to log.csv (optional)
#   --binary        Alias for --binary-mac (backward compat)
#   --binary-mac    Path to macOS ARM64 alfred_solver binary (default: dist/alfred_solver_mac)
#   --binary-linux  Path to Linux amd64 alfred_solver binary (default: dist/alfred_solver_linux)
#   --skip-binary   Omit binary from package (passed through to build_prod.sh)
#   --skip-linux    PROD only: produce only the macOS zip, skip the Linux zip
#   --build-linux   PROD only: auto-build the Linux binary via Docker before packaging
#
# Output (PROD):
#   builds/PROD/<release_id>_mac.zip    — macOS app bundle (Layer 2), unzip into customer's app/
#   builds/PROD/<release_id>_linux.zip  — Linux app bundle (Layer 2), unzip into customer's app/
#   releases/licenses/<release_id>.json — signed license (shared; send to customer separately)
#   releases/log.csv                    — two new rows appended (one per platform)
#
# Output (DEV):
#   builds/DEV/<release_id>.zip         — macOS app bundle only
#   releases/log.csv                    — one new row appended
#
# Two-layer delivery model:
#   PROD customers have a permanent installation root (Layer 1) containing .env,
#   license/, docker-compose.yml, and data/. The app bundle (Layer 2) lives in
#   app/ inside that root and is replaced wholesale on each update.
#   Use scripts/init_customer.sh to set up Layer 1 for a new customer.

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
BINARY_MAC="${REPO_ROOT}/dist/alfred_solver_mac"
BINARY_LINUX="${REPO_ROOT}/dist/alfred_solver_linux"
SKIP_BINARY=0
SKIP_LINUX=0
BUILD_LINUX=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
usage() {
    grep '^#' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --type)         BUILD_TYPE="$2";    shift 2 ;;
        --customer)     CUSTOMER="$2";      shift 2 ;;
        --code)         CODE="$2";          shift 2 ;;
        --expires)      EXPIRES="$2";       shift 2 ;;
        --notes)        NOTES="$2";         shift 2 ;;
        --binary)       BINARY_MAC="$2";    shift 2 ;;  # backward compat alias
        --binary-mac)   BINARY_MAC="$2";    shift 2 ;;
        --binary-linux) BINARY_LINUX="$2";  shift 2 ;;
        --skip-binary)  SKIP_BINARY=1;      shift   ;;
        --skip-linux)   SKIP_LINUX=1;       shift   ;;
        --build-linux)  BUILD_LINUX=1;      shift   ;;
        --help|-h)      usage ;;
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

# Force CODE to uppercase (tr used for macOS bash 3.2 compatibility)
CODE="$(echo "$CODE" | tr '[:lower:]' '[:upper:]')"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
RELEASES_DIR="${REPO_ROOT}/releases"
LICENSES_DIR="${RELEASES_DIR}/licenses"
LOG_CSV="${RELEASES_DIR}/log.csv"

BUILDS_DIR="${REPO_ROOT}/builds"
if [[ "$BUILD_TYPE" == "prod" ]]; then
    TYPE_DIR="${BUILDS_DIR}/PROD"
else
    TYPE_DIR="${BUILDS_DIR}/DEV"
fi

mkdir -p "${LICENSES_DIR}" "${TYPE_DIR}"

# ---------------------------------------------------------------------------
# Step 1: Determine release ID
# ---------------------------------------------------------------------------
ISSUED_AT="$(date +%Y-%m-%d)"

if [[ "$BUILD_TYPE" == "prod" ]]; then
    # Find the highest R<N> number in log.csv (PROD rows only).
    # Both R2_CLIENTE_mac and R2_CLIENTE_linux match R2, so the max is correct.
    if [[ -f "$LOG_CSV" ]]; then
        LAST_N=$(grep "^PROD," "$LOG_CSV" 2>/dev/null \
            | awk -F',' '{print $2}' \
            | grep -oE '^R[0-9]+' \
            | grep -oE '[0-9]+' \
            | sort -n \
            | tail -1 || true)
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
# Step 2 (PROD only): Optionally auto-build the Linux binary via Docker
# ---------------------------------------------------------------------------
if [[ "$BUILD_TYPE" == "prod" && $BUILD_LINUX -eq 1 ]]; then
    echo "==> Building Linux binary via Docker..."
    bash "${SCRIPT_DIR}/build_binary.sh" --platform linux
fi

# ---------------------------------------------------------------------------
# Step 3 (PROD only): Issue license — shared by both platform zips
# ---------------------------------------------------------------------------
LICENSE_OUT=""
if [[ "$BUILD_TYPE" == "prod" ]]; then
    LICENSE_OUT="${LICENSES_DIR}/${RELEASE_ID}.json"
    echo "==> Issuing license → ${LICENSE_OUT}"
    python "${REPO_ROOT}/license_tools/issue_license.py" \
        --customer "${CUSTOMER}" \
        --expires "${EXPIRES}" \
        --out "${LICENSE_OUT}"
    # License is NOT placed inside the app bundle — it lives in the customer's
    # Layer 1 installation root (license/) and is sent as a separate file.
fi

# ---------------------------------------------------------------------------
# Helper: build one platform's app bundle and zip it
#
# Usage: _build_platform_zip <platform> <binary_path> <zip_path>
#   platform    : "mac" or "linux"
#   binary_path : path to the platform binary (ignored when SKIP_BINARY=1)
#   zip_path    : destination zip file (e.g. builds/PROD/R2_CLIENTE_mac.zip)
#
# Always extracts as "app/" on the customer side.
# Prints the zip path on stdout.
# ---------------------------------------------------------------------------
_build_platform_zip() {
    local PLATFORM="$1"
    local BINARY_PATH="$2"
    local ZIP_PATH="$3"
    local BUILD_TMP="${TYPE_DIR}/build_tmp_${PLATFORM}"

    echo ""
    echo "==> [${PLATFORM}] Building app bundle..."
    rm -rf "${BUILD_TMP}"

    local BUILD_ARGS=("--output-dir" "${BUILD_TMP}")
    if [[ $SKIP_BINARY -eq 1 ]]; then
        BUILD_ARGS+=("--skip-binary")
    elif [[ "${PLATFORM}" == "linux" ]]; then
        BUILD_ARGS+=("--binary-linux" "${BINARY_PATH}")
    else
        BUILD_ARGS+=("--binary" "${BINARY_PATH}")
    fi

    bash "${SCRIPT_DIR}/build_prod.sh" "${BUILD_ARGS[@]}"

    # Zip extracts as "app/" on the customer side
    echo "==> [${PLATFORM}] Creating zip → ${ZIP_PATH}"
    mv "${BUILD_TMP}" "${TYPE_DIR}/app"
    (cd "${TYPE_DIR}" && zip -r "${ZIP_PATH}" "app" -x "app/.git/*")
    rm -rf "${TYPE_DIR}/app"
}

# ---------------------------------------------------------------------------
# Steps 4–5: Build app bundle(s) and zip
# ---------------------------------------------------------------------------
if [[ "$BUILD_TYPE" == "prod" ]]; then
    # Validate mac binary
    if [[ $SKIP_BINARY -eq 0 && ! -f "${BINARY_MAC}" ]]; then
        echo "ERROR: macOS binary not found at ${BINARY_MAC}" >&2
        echo "  Build it first: ./scripts/build_binary.sh --platform mac" >&2
        exit 1
    fi

    MAC_ZIP="${TYPE_DIR}/${RELEASE_ID}_mac.zip"
    _build_platform_zip "mac" "${BINARY_MAC}" "${MAC_ZIP}"
    MAC_HASH=""
    if [[ $SKIP_BINARY -eq 0 ]]; then
        MAC_HASH="$(shasum -a 256 "${BINARY_MAC}" | awk '{print $1}')"
    fi

    LINUX_ZIP=""
    LINUX_HASH=""
    if [[ $SKIP_LINUX -eq 0 ]]; then
        # Validate linux binary
        if [[ $SKIP_BINARY -eq 0 && ! -f "${BINARY_LINUX}" ]]; then
            echo "ERROR: Linux binary not found at ${BINARY_LINUX}" >&2
            echo "  Build it first: ./scripts/build_binary.sh --platform linux" >&2
            echo "  Or skip:        --skip-linux" >&2
            echo "  Or auto-build:  --build-linux" >&2
            exit 1
        fi
        LINUX_ZIP="${TYPE_DIR}/${RELEASE_ID}_linux.zip"
        _build_platform_zip "linux" "${BINARY_LINUX}" "${LINUX_ZIP}"
        if [[ $SKIP_BINARY -eq 0 ]]; then
            LINUX_HASH="$(shasum -a 256 "${BINARY_LINUX}" | awk '{print $1}')"
        fi
    fi

else
    # DEV: macOS only, single zip (no platform suffix)
    if [[ $SKIP_BINARY -eq 0 && ! -f "${BINARY_MAC}" ]]; then
        echo "ERROR: macOS binary not found at ${BINARY_MAC}" >&2
        echo "  Build it first: ./scripts/build_binary.sh --platform mac" >&2
        exit 1
    fi

    DEV_ZIP="${TYPE_DIR}/${RELEASE_ID}.zip"
    BUILD_TMP="${TYPE_DIR}/build_tmp_dev"

    echo ""
    echo "==> Building app bundle..."
    rm -rf "${BUILD_TMP}"

    BUILD_ARGS=("--output-dir" "${BUILD_TMP}")
    if [[ $SKIP_BINARY -eq 1 ]]; then
        BUILD_ARGS+=("--skip-binary")
    else
        BUILD_ARGS+=("--binary" "${BINARY_MAC}")
    fi
    bash "${SCRIPT_DIR}/build_prod.sh" "${BUILD_ARGS[@]}"

    echo "==> Creating zip → ${DEV_ZIP}"
    mv "${BUILD_TMP}" "${TYPE_DIR}/${RELEASE_ID}"
    (cd "${TYPE_DIR}" && zip -r "${DEV_ZIP}" "${RELEASE_ID}" -x "${RELEASE_ID}/.git/*")
    rm -rf "${TYPE_DIR}/${RELEASE_ID}"

    DEV_HASH=""
    if [[ $SKIP_BINARY -eq 0 ]]; then
        DEV_HASH="$(shasum -a 256 "${BINARY_MAC}" | awk '{print $1}')"
    fi
fi

# ---------------------------------------------------------------------------
# Step 6: Append to log.csv
# ---------------------------------------------------------------------------
echo ""
echo "==> Logging delivery..."
NOTES_SAFE="$(echo "$NOTES" | tr ',' ';')"
BUILD_TYPE_UPPER="$(echo "$BUILD_TYPE" | tr '[:lower:]' '[:upper:]')"

if [[ "$BUILD_TYPE" == "prod" ]]; then
    echo "PROD,${RELEASE_ID}_mac,${CUSTOMER},${ISSUED_AT},${EXPIRES},${MAC_HASH},${NOTES_SAFE}" >> "${LOG_CSV}"
    if [[ $SKIP_LINUX -eq 0 ]]; then
        echo "PROD,${RELEASE_ID}_linux,${CUSTOMER},${ISSUED_AT},${EXPIRES},${LINUX_HASH},${NOTES_SAFE}" >> "${LOG_CSV}"
    fi
else
    echo "DEV,${RELEASE_ID},${CUSTOMER},${ISSUED_AT},,${DEV_HASH},${NOTES_SAFE}" >> "${LOG_CSV}"
fi

# ---------------------------------------------------------------------------
# Step 7: Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Delivery complete: ${RELEASE_ID}"
echo "------------------------------------------------------------"
echo " Type:        ${BUILD_TYPE_UPPER}"
echo " Customer:    ${CUSTOMER}"
echo " Issued:      ${ISSUED_AT}"
if [[ "$BUILD_TYPE" == "prod" ]]; then
echo " Expires:     ${EXPIRES}"
echo ""
echo " App bundles (unzip into customer's installation root as app/):"
echo "   macOS:  ${MAC_ZIP}"
if [[ $SKIP_LINUX -eq 0 ]]; then
echo "   Linux:  ${LINUX_ZIP}"
fi
echo ""
echo " License:     ${LICENSE_OUT}"
echo "   → Send to customer separately; they place it in license/"
echo "   → This license is valid for all future builds (same keypair)"
fi
if [[ "$BUILD_TYPE" == "dev" ]]; then
echo " Zip:         ${DEV_ZIP}"
echo "   → Unzip into customer env's installation root as app/"
echo "   → To enable dev mode, add ALFRED_DEV_MODE=1 to the customer env's .env"
fi
if [[ "$BUILD_TYPE" == "prod" && -n "${MAC_HASH}" ]]; then
echo " Binary SHA (mac):   ${MAC_HASH}"
fi
if [[ "$BUILD_TYPE" == "prod" && $SKIP_LINUX -eq 0 && -n "${LINUX_HASH}" ]]; then
echo " Binary SHA (linux): ${LINUX_HASH}"
fi
if [[ "$BUILD_TYPE" == "dev" && -n "${DEV_HASH}" ]]; then
echo " Binary SHA:  ${DEV_HASH}"
fi
echo "============================================================"
