#!/usr/bin/env bash
# =============================================================================
# build_prod.sh — Generate the AlfredProd deliverable from AlfredDEV.
#
# Usage:
#   ./scripts/build_prod.sh --type prod|dev [--output-dir <path>] [--binary <path>] [--skip-binary]
#
# Options:
#   --type prod|dev       Build type (required). Controls output subdirectory:
#                           prod → builds/PROD/AlfredProd
#                           dev  → builds/DEV/AlfredProd
#   --output-dir <path>   Override output directory (optional; used internally by deliver.sh).
#   --binary <path>       Path to the alfred_solver binary to ship.
#                         Default: dist/alfred_solver (built by PyInstaller here).
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
#   7. Updates .gitignore to exclude bin/ from version control.
#
# After running, AlfredProd is a fully functional, self-contained repo.
# The customer receives AlfredProd + the binary (separately or together).
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
BINARY_PATH="${DEV_ROOT}/dist/alfred_solver"
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
        prod) OUTPUT_DIR="${DEV_ROOT}/builds/PROD/AlfredProd" ;;
        dev)  OUTPUT_DIR="${DEV_ROOT}/builds/DEV/AlfredProd" ;;
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
    if [[ ! -f "${BINARY_PATH}" ]]; then
        echo ""
        echo "ERROR: Binary not found at ${BINARY_PATH}"
        echo "  Build it first:  cd ${DEV_ROOT} && pyinstaller alfred_solver.spec --distpath dist/"
        echo "  Or skip:         --skip-binary"
        exit 1
    fi
    echo "Binary  : ${BINARY_PATH}"
fi
echo ""

# ---------------------------------------------------------------------------
# Step 1: Clean and recreate output directory
# ---------------------------------------------------------------------------
echo "[1/7] Clearing output directory..."
if [[ -d "${OUTPUT_DIR}" ]]; then
    rm -rf "${OUTPUT_DIR}"
fi
mkdir -p "${OUTPUT_DIR}"

# ---------------------------------------------------------------------------
# Step 2: Copy AlfredDEV → AlfredProd, excluding hidden files
# ---------------------------------------------------------------------------
echo "[2/7] Copying AlfredDEV to AlfredProd (excluding algorithm sources)..."

rsync -a \
    --exclude=".git" \
    --exclude=".env" \
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
    --exclude="output/" \
    --exclude="data/runs/" \
    --exclude="data/api_snapshots/" \
    --exclude="data/profiling/" \
    --exclude="data/examples/" \
    --exclude="request.json" \
    --exclude="request2.json" \
    --exclude="request/" \
    --exclude="validate_deployment.py" \
    --exclude=".env.docker" \
    --exclude="Dockerfile" \
    --exclude="docker-compose.yml" \
    "${DEV_ROOT}/" \
    "${OUTPUT_DIR}/"

# ---------------------------------------------------------------------------
# Step 2b: Install prod README (replaces the dev README copied by rsync)
# ---------------------------------------------------------------------------
echo "[2b/7] Installing prod README..."
cp "${DEV_ROOT}/docs/README_PROD.md" "${OUTPUT_DIR}/README.md"

# ---------------------------------------------------------------------------
# Step 3: Copy serde.py → src/optimization/solver_serde.py
# ---------------------------------------------------------------------------
echo "[3/7] Installing serde module..."
cp "${DEV_ROOT}/src/solver_executable/serde.py" \
   "${OUTPUT_DIR}/src/alfred/optimization/solver_serde.py"

if [[ ! -s "${OUTPUT_DIR}/src/alfred/optimization/solver_serde.py" ]]; then
    echo "ERROR: Failed to install serde module — ${OUTPUT_DIR}/src/alfred/optimization/solver_serde.py is missing or empty" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 4: Copy solver_bridge.py → src/optimization/solver_bridge.py
# ---------------------------------------------------------------------------
echo "[4/7] Installing solver_bridge..."
cp "${DEV_ROOT}/src/alfred/optimization/solver_bridge.py" \
   "${OUTPUT_DIR}/src/alfred/optimization/solver_bridge.py"

# ---------------------------------------------------------------------------
# Step 5: Create stub algorithms package (preserves import structure)
# ---------------------------------------------------------------------------
echo "[5/7] Creating algorithms stub package..."
mkdir -p "${OUTPUT_DIR}/src/alfred/optimization/algorithms"
cat > "${OUTPUT_DIR}/src/alfred/optimization/algorithms/__init__.py" << 'STUB'
# This package is intentionally empty in AlfredProd.
# Algorithm logic is encapsulated in the alfred_solver binary.
STUB

# ---------------------------------------------------------------------------
# Step 6: Install probe_bridge + patch feasibility_probe.py
# ---------------------------------------------------------------------------
echo "[6/7] Installing probe_bridge and patching feasibility_probe..."
cp "${DEV_ROOT}/src/alfred/availability/probe_bridge.py" \
   "${OUTPUT_DIR}/src/alfred/availability/probe_bridge.py"

# Patch feasibility_probe.py: swap run_insertion_worker import
PROBE_FILE="${OUTPUT_DIR}/src/alfred/availability/feasibility_probe.py"
if [[ -f "${PROBE_FILE}" ]]; then
    # Replace the two-line import block with the bridge import
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
    "from alfred.availability.probe_bridge import run_insertion_worker_bridge as run_insertion_worker\n"
    "from alfred.optimization.algorithms.insert.insert_algorithms import get_drivers"
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

# Also patch main.py: swap OptimizationSolver import
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
    echo "ERROR: main.py not found at ${MAIN_FILE}" >&2
    echo "  This file should have been copied by rsync in step 2." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 7: Install binary + license placeholder
# ---------------------------------------------------------------------------
if [[ "${SKIP_BINARY}" == false ]]; then
    echo "[7/7] Installing alfred_solver binary..."
    mkdir -p "${OUTPUT_DIR}/bin"
    cp "${BINARY_PATH}" "${OUTPUT_DIR}/bin/alfred_solver"
    chmod +x "${OUTPUT_DIR}/bin/alfred_solver"
else
    echo "[7/7] Skipping binary installation (--skip-binary)."
    mkdir -p "${OUTPUT_DIR}/bin"
    cat > "${OUTPUT_DIR}/bin/README.md" << 'BINREADME'
# bin/

Place the `alfred_solver` binary here before running AlfredProd.

The binary is built from AlfredDEV:

    cd <AlfredDEV>
    pyinstaller alfred_solver.spec --distpath dist/
    cp dist/alfred_solver <AlfredProd>/bin/alfred_solver
    chmod +x <AlfredProd>/bin/alfred_solver

The binary must match the OS/architecture of the machine running AlfredProd.
BINREADME
fi

# Create license/ directory placeholder
mkdir -p "${OUTPUT_DIR}/license"
cat > "${OUTPUT_DIR}/license/README.md" << 'LICREADME'
# license/

Place the license file issued for this deployment here, e.g.:

    license/alfred_license.json

Then set the environment variable so the solver binary can find it:

    export ALFRED_LICENSE=/path/to/AlfredProd/license/alfred_license.json

Or add it to your .env file:

    ALFRED_LICENSE=./license/alfred_license.json

The binary verifies the license on every invocation. It will exit with code 5
if the file is missing, tampered with, or expired.

License files are issued by the AlfredDEV team. Contact them to obtain or
renew a license. See license_tools/README.md in the AlfredDEV repo for
the full operator guide.
LICREADME

# Update .gitignore: exclude bin/ and license/*.json (never commit binaries or license files)
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
_append_gitignore "license/*.json" "License files (customer-specific, do not commit)"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "=== Build complete ==="
echo "AlfredProd is ready at: ${OUTPUT_DIR}"
echo ""
echo "Quick sanity check:"
echo "  cd ${OUTPUT_DIR}"
echo "  python -c 'from alfred.optimization.solver_bridge import SolverBridge; print(\"OK\")'"
echo "  python -c 'from alfred.availability.probe_bridge import run_insertion_worker_bridge; print(\"OK\")'"
if [[ "${SKIP_BINARY}" == false ]]; then
    echo "  bin/alfred_solver --help"
fi
echo ""
echo "Before running, set the license path:"
echo "  export ALFRED_LICENSE=${OUTPUT_DIR}/license/alfred_license.json"
echo "  (place the issued license file there first)"
