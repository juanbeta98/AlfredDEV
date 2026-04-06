#!/usr/bin/env bash
# =============================================================================
# prod_deliver.sh — Build, package, and install a prod build in one command.
#
# Usage:
#   ./scripts/prod_deliver.sh --customer "Cliente S.A." --code CLIENTE \
#                             --expires 2026-12-31 [--notes "Initial delivery"] \
#                             [--dummy] [--skip-linux]
#
# Options:
#   --customer    Customer name for license payload (required)
#   --code        Short uppercase code for release ID, e.g. CLIENTE (required)
#   --expires     License expiry date YYYY-MM-DD (required)
#   --notes       Free-text notes appended to log.csv (optional)
#   --dummy       Test build — skips license issuance and log.csv entry.
#                 Reuses the license already installed in the customer environment.
#   --skip-linux  Skip Linux binary build and Linux zip (mac only)
#   --help        Show this message.
#
# What it does:
#   1. Builds the alfred_solver binary for macOS via build_binary.sh
#   2. Builds the alfred_solver binary for Linux via build_binary.sh (skipped with --skip-linux)
#   3. Creates a PROD package via deliver.sh (mac + linux zips, license)
#   4. Installs the Linux zip into the ALFRED_env via its install.sh
#      (Mac zip and license are left for manual delivery to the customer)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ALFRED_ENV="$(cd "${REPO_ROOT}/.." && pwd)/ALFRED_env"

CUSTOMER=""
CODE=""
EXPIRES=""
NOTES=""
DUMMY=0
SKIP_LINUX=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --customer)   CUSTOMER="$2";  shift 2 ;;
        --code)       CODE="$2";      shift 2 ;;
        --expires)    EXPIRES="$2";   shift 2 ;;
        --notes)      NOTES="$2";     shift 2 ;;
        --dummy)      DUMMY=1;        shift   ;;
        --skip-linux) SKIP_LINUX=1;   shift   ;;
        --help|-h)
            sed -n '2,27p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "${CUSTOMER}" ]]; then
    echo "ERROR: --customer is required" >&2; exit 1
fi
if [[ -z "${CODE}" ]]; then
    echo "ERROR: --code is required" >&2; exit 1
fi
if [[ -z "${EXPIRES}" ]]; then
    echo "ERROR: --expires is required" >&2; exit 1
fi

if [[ ! -d "${ALFRED_ENV}" ]]; then
    echo "ERROR: ALFRED_env not found at ${ALFRED_ENV}" >&2
    exit 1
fi

if [[ ! -f "${ALFRED_ENV}/install.sh" ]]; then
    echo "ERROR: install.sh not found in ${ALFRED_ENV}" >&2
    exit 1
fi

echo "============================================================"
echo " prod_deliver.sh"
echo " Customer : ${CUSTOMER}"
echo " Code     : ${CODE}"
echo " Expires  : ${EXPIRES}"
echo " Env      : ${ALFRED_ENV}"
if [[ $DUMMY -eq 1 ]]; then
echo " Mode     : DUMMY (test build)"
fi
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Build mac binary
# ---------------------------------------------------------------------------
echo "=== [1/4] Building macOS binary ==="
bash "${SCRIPT_DIR}/build_binary.sh" --platform mac

# ---------------------------------------------------------------------------
# Step 2: Build linux binary
# ---------------------------------------------------------------------------
if [[ $SKIP_LINUX -eq 0 ]]; then
    echo ""
    echo "=== [2/4] Building Linux binary ==="
    bash "${SCRIPT_DIR}/build_binary.sh" --platform linux
else
    echo ""
    echo "=== [2/4] Skipping Linux binary (--skip-linux) ==="
fi

# ---------------------------------------------------------------------------
# Step 3: Deliver (capture output to extract zip paths)
# ---------------------------------------------------------------------------
echo ""
echo "=== [3/4] Creating PROD package ==="

DELIVER_ARGS=(
    --type prod
    --customer "${CUSTOMER}"
    --code "${CODE}"
    --expires "${EXPIRES}"
)
[[ -n "${NOTES}" ]]   && DELIVER_ARGS+=(--notes "${NOTES}")
[[ $DUMMY -eq 1 ]]    && DELIVER_ARGS+=(--dummy)
[[ $SKIP_LINUX -eq 1 ]] && DELIVER_ARGS+=(--skip-linux)

DELIVER_OUTPUT="$(bash "${SCRIPT_DIR}/deliver.sh" \
    "${DELIVER_ARGS[@]}" \
    2>&1 | tee /dev/stderr)"

# Extract zip paths from deliver.sh summary lines:
#   "   macOS:  builds/PROD/..."
#   "   Linux:  builds/PROD/..."
MAC_ZIP_REL="$(echo "${DELIVER_OUTPUT}" | grep -E '^\s+macOS:' | awk '{print $2}')"
LINUX_ZIP_REL=""
if [[ $SKIP_LINUX -eq 0 ]]; then
    LINUX_ZIP_REL="$(echo "${DELIVER_OUTPUT}" | grep -E '^\s+Linux:' | awk '{print $2}')"
fi

if [[ -z "${MAC_ZIP_REL}" ]]; then
    echo "ERROR: Could not determine mac zip path from deliver.sh output." >&2
    exit 1
fi
if [[ $SKIP_LINUX -eq 0 && -z "${LINUX_ZIP_REL}" ]]; then
    echo "ERROR: Could not determine linux zip path from deliver.sh output." >&2
    exit 1
fi

# Resolve relative paths against repo root
_resolve() {
    local P="$1"
    if [[ "${P}" = /* ]]; then echo "${P}"; else echo "${REPO_ROOT}/${P}"; fi
}

MAC_ZIP="$(_resolve "${MAC_ZIP_REL}")"

if [[ ! -f "${MAC_ZIP}" ]]; then
    echo "ERROR: Expected mac zip not found: ${MAC_ZIP}" >&2
    exit 1
fi

if [[ $SKIP_LINUX -eq 0 ]]; then
    LINUX_ZIP="$(_resolve "${LINUX_ZIP_REL}")"
    if [[ ! -f "${LINUX_ZIP}" ]]; then
        echo "ERROR: Expected linux zip not found: ${LINUX_ZIP}" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Step 4: Install linux build into ALFRED_env
# ---------------------------------------------------------------------------
echo ""
if [[ $SKIP_LINUX -eq 0 ]]; then
    echo "=== [4/4] Installing Linux build into ALFRED_env ==="
    bash "${ALFRED_ENV}/install.sh" "${LINUX_ZIP}"
else
    echo "=== [4/4] Skipping install (no Linux zip — --skip-linux was set) ==="
fi

echo ""
echo "============================================================"
echo " prod_deliver.sh done"
echo ""
echo " Mac zip  : ${MAC_ZIP}"
if [[ $SKIP_LINUX -eq 0 ]]; then
echo " Linux zip: ${LINUX_ZIP}  ← installed into ALFRED_env"
fi
echo " Env      : ${ALFRED_ENV}"
echo ""
echo " Send to customer:"
echo "   - Mac zip (for macOS deployments)"
if [[ $SKIP_LINUX -eq 0 ]]; then
echo "   - Linux zip (for Linux/Docker deployments)"
fi
if [[ $DUMMY -eq 0 ]]; then
    LICENSE_OUT="$(echo "${DELIVER_OUTPUT}" | grep -E '^\s+License:' | head -1 | awk '{print $2}')"
    if [[ -n "${LICENSE_OUT}" ]]; then
echo "   - License: ${LICENSE_OUT}"
    fi
fi
echo "============================================================"
