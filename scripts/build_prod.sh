#!/usr/bin/env bash
# =============================================================================
# build_prod.sh — Generate the AlfredProd app bundle from AlfredDEV.
#
# Usage:
#   ./scripts/build_prod.sh --type prod|dev [--output-dir <path>] [--binary <path>] [--binary-linux <path>] [--skip-binary]
#
# Options:
#   --type prod|dev       Build type (required). Controls output subdirectory:
#                           prod → builds/PROD/app
#                           dev  → builds/DEV/app
#   --output-dir <path>   Override output directory (optional; used internally by deliver.sh).
#   --binary <path>       Path to the macOS ARM64 alfred_solver binary to ship.
#                         Default: dist/alfred_solver_mac
#   --binary-linux <path> Path to a Linux amd64 alfred_solver binary to ship.
#                         Mutually exclusive with --binary.
#   --skip-binary         Don't copy a binary (useful when building the binary separately).
#   --help                Show this message.
#
# What it does:
#   1. Copies AlfredDEV to <output-dir>, excluding hidden algorithm/solver files.
#   2. Replaces src/optimization/solver.py with solver_bridge.py.
#   3. Replaces src/availability/feasibility_probe.py with the probe-bridge variant.
#   4. Copies serde.py to src/optimization/solver_serde.py.
#   5. Adds the alfred_solver binary to bin/.
#   6. Creates a stub src/optimization/algorithms/__init__.py.
#
# Output:
#   The app bundle — source + bridges + binary. No .env, no docker-compose,
#   no license. Those live in the customer's Layer 1 installation root and
#   are never overwritten by app updates.
#   Use scripts/init_customer.sh (once per new customer) to produce Layer 1.
#
# Update procedure (when AlfredDEV changes):
#   1. Make and test changes in AlfredDEV normally.
#   2. If the solver changed:  rebuild the binary (pyinstaller alfred_solver.spec --distpath dist/)
#   3. Run this script again.  AlfredProd is regenerated from scratch — no manual sync needed.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_TYPE=""
OUTPUT_DIR=""
BINARY_PATH="${DEV_ROOT}/dist/alfred_solver_mac"
BINARY_LINUX_PATH=""
PLATFORM_LABEL="mac"
SKIP_BINARY=false

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --type)
            BUILD_TYPE="$2"; shift 2 ;;
        --output-dir)
            OUTPUT_DIR="$2"; shift 2 ;;
        --binary)
            BINARY_PATH="$2"; shift 2 ;;
        --binary-linux)
            BINARY_LINUX_PATH="$2"; shift 2 ;;
        --skip-binary)
            SKIP_BINARY=true; shift ;;
        --help|-h)
            sed -n '2,40p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *)
            echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Resolve output directory
# ---------------------------------------------------------------------------
if [[ -z "${OUTPUT_DIR}" ]]; then
    if [[ -z "${BUILD_TYPE}" ]]; then
        echo "ERROR: --type prod|dev is required (or provide --output-dir explicitly)" >&2
        exit 1
    fi
    case "${BUILD_TYPE}" in
        prod) OUTPUT_DIR="${DEV_ROOT}/builds/PROD/app" ;;
        dev)  OUTPUT_DIR="${DEV_ROOT}/builds/DEV/app" ;;
        *)    echo "ERROR: --type must be 'prod' or 'dev'" >&2; exit 1 ;;
    esac
fi

# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------
echo "=== AlfredProd Build ==="
echo "Source  : ${DEV_ROOT}"
echo "Output  : ${OUTPUT_DIR}"

if [[ "${SKIP_BINARY}" == false ]]; then
    # Resolve which binary to use: --binary-linux takes precedence if set
    if [[ -n "${BINARY_LINUX_PATH}" && "${BINARY_PATH}" != "${DEV_ROOT}/dist/alfred_solver_mac" ]]; then
        echo "ERROR: --binary and --binary-linux are mutually exclusive" >&2
        exit 1
    fi
    if [[ -n "${BINARY_LINUX_PATH}" ]]; then
        BINARY_PATH="${BINARY_LINUX_PATH}"
        PLATFORM_LABEL="linux"
    fi
    if [[ ! -f "${BINARY_PATH}" ]]; then
        echo ""
        echo "ERROR: Binary not found at ${BINARY_PATH}"
        echo "  Build it first:  ./scripts/build_binary.sh --platform ${PLATFORM_LABEL}"
        echo "  Or skip:         --skip-binary"
        exit 1
    fi
    echo "Binary  : ${BINARY_PATH} (${PLATFORM_LABEL})"
fi
echo ""

# ---------------------------------------------------------------------------
# Step 1: Clean and recreate output directory
# ---------------------------------------------------------------------------
echo "[1/6] Clearing output directory..."
if [[ -d "${OUTPUT_DIR}" ]]; then
    rm -rf "${OUTPUT_DIR}"
fi
mkdir -p "${OUTPUT_DIR}"

# ---------------------------------------------------------------------------
# Step 2: Copy AlfredDEV → app bundle, excluding proprietary/infra files
# ---------------------------------------------------------------------------
echo "[2/6] Copying AlfredDEV to app bundle (excluding algorithm sources)..."

rsync -a \
    --exclude=".git" \
    --exclude=".env" \
    --exclude=".env.docker" \
    --exclude=".env.template" \
    --exclude="LICENSE" \
    --exclude="license" \
    --exclude="__pycache__" \
    --exclude="*.py[cod]" \
    --exclude="*.egg-info" \
    --exclude=".DS_Store" \
    --exclude="dist/" \
    --exclude="build/" \
    --exclude="*.spec" \
    --exclude="src/solver_executable/" \
    --exclude="scripts/" \
    --exclude="src/alfred/optimization/solver.py" \
    --exclude="src/alfred/optimization/algorithms/" \
    --exclude="src/alfred/optimization/solver_bridge.py" \
    --exclude="src/alfred/availability/probe_bridge.py" \
    --exclude="notebooks/" \
    --exclude="docs/" \
    --exclude="tests/" \
    --exclude="experiments/" \
    --exclude="license_tools/" \
    --exclude="releases/" \
    --exclude="builds/" \
    --exclude="osrm_resources/" \
    --exclude="osrm_resources.zip" \
    --exclude="output/" \
    --exclude="data/runs/" \
    --exclude="data/api_snapshots/" \
    --exclude="data/profiling/" \
    --exclude="data/examples/" \
    --exclude="request.json" \
    --exclude="request2.json" \
    --exclude="request_avail.json" \
    --exclude="request/" \
    --exclude="validate_deployment.py" \
    --exclude="docker-compose.yml" \
    --exclude="res/" \
    "${DEV_ROOT}/" \
    "${OUTPUT_DIR}/"

# Install prod README (replaces the dev README copied by rsync)
echo "[2b/6] Installing prod README..."
cp "${DEV_ROOT}/docs/README_PROD.md" "${OUTPUT_DIR}/README.md"

# ---------------------------------------------------------------------------
# Step 3: Copy serde.py → src/optimization/solver_serde.py
# ---------------------------------------------------------------------------
echo "[3/6] Installing serde module..."
cp "${DEV_ROOT}/src/solver_executable/serde.py" \
   "${OUTPUT_DIR}/src/alfred/optimization/solver_serde.py"

if [[ ! -s "${OUTPUT_DIR}/src/alfred/optimization/solver_serde.py" ]]; then
    echo "ERROR: Failed to install serde module — ${OUTPUT_DIR}/src/alfred/optimization/solver_serde.py is missing or empty" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 4: Copy solver_bridge.py → src/optimization/solver_bridge.py
# ---------------------------------------------------------------------------
echo "[4/6] Installing solver_bridge..."
cp "${DEV_ROOT}/src/alfred/optimization/solver_bridge.py" \
   "${OUTPUT_DIR}/src/alfred/optimization/solver_bridge.py"

# ---------------------------------------------------------------------------
# Step 5: Create stub algorithms package (preserves import structure)
# ---------------------------------------------------------------------------
echo "[5/6] Creating algorithms stub package..."
mkdir -p "${OUTPUT_DIR}/src/alfred/optimization/algorithms"
cat > "${OUTPUT_DIR}/src/alfred/optimization/algorithms/__init__.py" << 'STUB'
# This package is intentionally empty in AlfredProd.
# Algorithm logic is encapsulated in the alfred_solver binary.
STUB

# Install probe_bridge + patch feasibility_probe.py
cp "${DEV_ROOT}/src/alfred/availability/probe_bridge.py" \
   "${OUTPUT_DIR}/src/alfred/availability/probe_bridge.py"

# Patch feasibility_probe.py: swap run_insertion_worker import
PROBE_FILE="${OUTPUT_DIR}/src/alfred/availability/feasibility_probe.py"
if [[ -f "${PROBE_FILE}" ]]; then
    python3 - "${PROBE_FILE}" << 'PATCHSCRIPT'
import sys, re

path = sys.argv[1]
text = open(path).read()

old = (
    "from alfred.optimization.algorithms.insert.insert_algorithms import (\n"
    "    get_drivers,\n"
    "    run_insertion_worker,\n"
    ")"
)
new = (
    "from alfred.availability.probe_bridge import (\n"
    "    run_insertion_worker_bridge as run_insertion_worker,\n"
    "    get_drivers,\n"
    ")"
)

if old not in text:
    print(f"ERROR: expected import block not found in {path}", file=sys.stderr)
    print("  The import to replace was:", file=sys.stderr)
    print(f"    {old!r}", file=sys.stderr)
    sys.exit(1)

patched = text.replace(old, new, 1)
open(path, "w").write(patched)
print(f"  Patched: {path}")
PATCHSCRIPT
else
    echo "ERROR: feasibility_probe.py not found at ${PROBE_FILE}" >&2
    echo "  This file should have been copied by rsync in step 2." >&2
    exit 1
fi

# Patch preassigned.py: swap offline_algorithms import to probe_bridge
PREASSIGNED_FILE="${OUTPUT_DIR}/src/alfred/optimization/common/preassigned.py"
if [[ -f "${PREASSIGNED_FILE}" ]]; then
    python3 - "${PREASSIGNED_FILE}" << 'PATCHSCRIPT'
import sys

path = sys.argv[1]
text = open(path).read()

old = "from alfred.optimization.algorithms.offline.offline_algorithms import assign_task_to_driver, init_drivers"
new = "from alfred.availability.probe_bridge import assign_task_to_driver, init_drivers"

if old not in text:
    print(f"ERROR: expected import not found in {path}", file=sys.stderr)
    print(f"  Expected: {old!r}", file=sys.stderr)
    sys.exit(1)

patched = text.replace(old, new, 1)
open(path, "w").write(patched)
print(f"  Patched: {path}")
PATCHSCRIPT
else
    echo "ERROR: preassigned.py not found at ${PREASSIGNED_FILE}" >&2
    echo "  This file should have been copied by rsync in step 2." >&2
    exit 1
fi

# Patch orchestrator.py: swap OptimizationSolver import
MAIN_FILE="${OUTPUT_DIR}/src/alfred/pipeline/orchestrator.py"
if [[ -f "${MAIN_FILE}" ]]; then
    python3 - "${MAIN_FILE}" << 'PATCHSCRIPT'
import sys

path = sys.argv[1]
text = open(path).read()

old = "from alfred.optimization.solver import OptimizationSolver"
new = "from alfred.optimization.solver_bridge import SolverBridge as OptimizationSolver"

if old not in text:
    print(f"ERROR: expected import not found in {path}", file=sys.stderr)
    print(f"  Expected: {old!r}", file=sys.stderr)
    sys.exit(1)

patched = text.replace(old, new, 1)
open(path, "w").write(patched)
print(f"  Patched: {path}")
PATCHSCRIPT
else
    echo "ERROR: orchestrator.py not found at ${MAIN_FILE}" >&2
    echo "  This file should have been copied by rsync in step 2." >&2
    exit 1
fi

# Patch pipeline entry points: harden _validate_license() — PROD only.
# Remove the ALFRED_DEV_MODE early-return so license check is unconditional in PROD.
if [[ "${BUILD_TYPE}" != "prod" ]]; then
    echo "  Skipping _validate_license hardening (dev build)."
else
for PIPELINE_FILE in \
    "${OUTPUT_DIR}/src/alfred/pipeline/check_availability.py" \
    "${OUTPUT_DIR}/src/alfred/pipeline/orchestrator.py"; do
    if [[ -f "${PIPELINE_FILE}" ]]; then
        python3 - "${PIPELINE_FILE}" << 'PATCHSCRIPT'
import sys

path = sys.argv[1]
text = open(path).read()

old = (
    "def _validate_license() -> None:\n"
    "    dev_mode = os.environ.get(\"ALFRED_DEV_MODE\", \"\").strip() not in (\"\", \"0\")\n"
    "    if dev_mode:\n"
    "        return\n"
    "    license_path = os.environ.get(\"ALFRED_LICENSE\")\n"
)
new = (
    "def _validate_license() -> None:\n"
    "    license_path = os.environ.get(\"ALFRED_LICENSE\")\n"
)

if old not in text:
    print(f"ERROR: expected _validate_license pattern not found in {path}", file=sys.stderr)
    sys.exit(1)

patched = text.replace(old, new, 1)
open(path, "w").write(patched)
print(f"  Patched: {path}")
PATCHSCRIPT
    else
        echo "ERROR: ${PIPELINE_FILE} not found" >&2
        exit 1
    fi
done
fi  # end BUILD_TYPE == prod

# ---------------------------------------------------------------------------
# Step 6: Install binary
# ---------------------------------------------------------------------------
if [[ "${SKIP_BINARY}" == false ]]; then
    echo "[6/6] Installing alfred_solver binary..."
    mkdir -p "${OUTPUT_DIR}/bin"
    cp "${BINARY_PATH}" "${OUTPUT_DIR}/bin/alfred_solver"
    chmod +x "${OUTPUT_DIR}/bin/alfred_solver"
else
    echo "[6/6] Skipping binary installation (--skip-binary)."
    mkdir -p "${OUTPUT_DIR}/bin"
    cat > "${OUTPUT_DIR}/bin/README.md" << 'BINREADME'
# bin/

Place the `alfred_solver` binary here before running AlfredProd.

The binary is built from AlfredDEV:

    cd <AlfredDEV>
    ./scripts/build_binary.sh --platform mac|linux
    cp dist/alfred_solver_mac <AlfredProd>/bin/alfred_solver   # macOS ARM64
    cp dist/alfred_solver_linux <AlfredProd>/bin/alfred_solver  # Linux amd64
    chmod +x <AlfredProd>/bin/alfred_solver

The binary must match the OS/architecture of the machine running AlfredProd.
BINREADME
fi

# Update .gitignore: exclude bin/ (never commit binaries)
GITIGNORE="${OUTPUT_DIR}/.gitignore"
_append_gitignore() {
    local pattern="$1"
    local comment="$2"
    if [[ -f "${GITIGNORE}" ]]; then
        if ! grep -qF "${pattern}" "${GITIGNORE}"; then
            printf "\n# %s\n%s\n" "${comment}" "${pattern}" >> "${GITIGNORE}"
        fi
    else
        printf "# %s\n%s\n" "${comment}" "${pattern}" > "${GITIGNORE}"
    fi
}
_append_gitignore "bin/" "Compiled solver binary (install separately, do not commit)"

# ---------------------------------------------------------------------------
# Step 7 (dev only): seed repo-root bin/ so `docker compose build alfred` works
# ---------------------------------------------------------------------------
if [[ "${BUILD_TYPE}" == "dev" && "${SKIP_BINARY}" == false ]]; then
    echo "[7] Seeding repo-root bin/ for Docker builds..."
    mkdir -p "${DEV_ROOT}/bin"
    cp "${BINARY_PATH}" "${DEV_ROOT}/bin/alfred_solver"
    chmod +x "${DEV_ROOT}/bin/alfred_solver"
    echo "  Seeded: ${DEV_ROOT}/bin/alfred_solver"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "=== Build complete ==="
echo "App bundle ready at: ${OUTPUT_DIR}"
echo ""
echo "Quick sanity check:"
echo "  cd ${OUTPUT_DIR}"
echo "  python -c 'from alfred.optimization.solver_bridge import SolverBridge; print(\"OK\")'"
echo "  python -c 'from alfred.availability.probe_bridge import run_insertion_worker_bridge; print(\"OK\")'"
if [[ "${SKIP_BINARY}" == false ]]; then
    echo "  bin/alfred_solver --help"
fi
echo ""
echo "NOTE: This is the app bundle only (Layer 2)."
echo "  For a new customer, first run:  ./scripts/init_customer.sh --customer <name> --code <CODE>"
echo "  Then deliver the app bundle:    ./scripts/deliver.sh --type prod ..."
