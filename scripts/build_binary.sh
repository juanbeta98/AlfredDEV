#!/usr/bin/env bash
# =============================================================================
# build_binary.sh — Build the alfred_solver binary for one or both platforms.
#
# Usage:
#   ./scripts/build_binary.sh --platform mac|linux|all
#
# Options:
#   --platform mac     Build for macOS ARM64 using local PyInstaller.
#                      Output: dist/alfred_solver_mac
#   --platform linux   Build for Linux amd64 using Docker (linux/amd64 container).
#                      Output: dist/alfred_solver_linux
#   --platform all     Build both. Equivalent to running both in sequence.
#   --help             Show this message.
#
# Prerequisites:
#   mac:   pyinstaller installed in the active Python environment
#   linux: Docker running with linux/amd64 support (Docker Desktop on Mac uses QEMU)
#
# The dist/ directory is created if it does not exist.
# The binary is always built from the current source tree.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
PLATFORM=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform) PLATFORM="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,24p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "${PLATFORM}" ]]; then
    echo "ERROR: --platform mac|linux|all is required" >&2
    exit 1
fi

if [[ "${PLATFORM}" != "mac" && "${PLATFORM}" != "linux" && "${PLATFORM}" != "all" ]]; then
    echo "ERROR: --platform must be mac, linux, or all" >&2
    exit 1
fi

mkdir -p "${DIST_DIR}"

# ---------------------------------------------------------------------------
# Mac build: run PyInstaller natively
# ---------------------------------------------------------------------------
_build_mac() {
    echo "=== Building macOS ARM64 binary ==="
    cd "${REPO_ROOT}"

    if ! command -v pyinstaller &>/dev/null; then
        echo "ERROR: pyinstaller not found. Install it: pip install pyinstaller" >&2
        exit 1
    fi

    # Clean previous outputs to avoid stale artifacts
    rm -f "${DIST_DIR}/alfred_solver" "${DIST_DIR}/alfred_solver_mac"

    pyinstaller alfred_solver.spec --distpath "${DIST_DIR}/"

    # PyInstaller outputs 'alfred_solver'; rename to platform-specific name
    mv "${DIST_DIR}/alfred_solver" "${DIST_DIR}/alfred_solver_mac"
    chmod +x "${DIST_DIR}/alfred_solver_mac"

    echo ""
    echo "==> macOS binary: ${DIST_DIR}/alfred_solver_mac"
    echo "    sha256: $(shasum -a 256 "${DIST_DIR}/alfred_solver_mac" | awk '{print $1}')"
}

# ---------------------------------------------------------------------------
# Linux build: run PyInstaller inside a Docker container (linux/amd64)
# ---------------------------------------------------------------------------
_build_linux() {
    echo "=== Building Linux amd64 binary via Docker ==="

    if ! docker info &>/dev/null; then
        echo "ERROR: Docker is not running or not accessible." >&2
        exit 1
    fi

    IMAGE_TAG="alfred-builder:latest"

    echo "==> Building Docker image (platform: linux/amd64)..."
    docker build \
        --no-cache \
        --platform linux/amd64 \
        -f "${REPO_ROOT}/Dockerfile.builder" \
        -t "${IMAGE_TAG}" \
        "${REPO_ROOT}"

    # Clean previous linux binary
    rm -f "${DIST_DIR}/alfred_solver_linux"

    echo "==> Running builder container..."
    docker run --rm \
        --platform linux/amd64 \
        -v "${DIST_DIR}:/output" \
        "${IMAGE_TAG}"

    if [[ ! -f "${DIST_DIR}/alfred_solver_linux" ]]; then
        echo "ERROR: Docker build did not produce dist/alfred_solver_linux" >&2
        exit 1
    fi

    chmod +x "${DIST_DIR}/alfred_solver_linux"

    echo ""
    echo "==> Linux binary: ${DIST_DIR}/alfred_solver_linux"
    echo "    sha256: $(shasum -a 256 "${DIST_DIR}/alfred_solver_linux" | awk '{print $1}')"
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
case "${PLATFORM}" in
    mac)   _build_mac ;;
    linux) _build_linux ;;
    all)   _build_mac; echo ""; _build_linux ;;
esac

echo ""
echo "=== build_binary.sh done ==="
