# -*- mode: python ; coding: utf-8 -*-
#
# alfred_solver.spec — PyInstaller build specification for the alfred_solver binary.
#
# Build from the AlfredDEV repo root:
#
#   pip install pyinstaller
#   pyinstaller alfred_solver.spec --distpath dist/
#
# Output:
#   dist/alfred_solver          (Linux / macOS)
#   dist/alfred_solver.exe      (Windows)
#
# The binary must be built on the TARGET OS/architecture — a Linux binary
# will not run on macOS, and vice versa.  Build on a Linux machine (or a
# Linux Docker container) for server deployment.
#
# After building, copy the binary to AlfredProd:
#   cp dist/alfred_solver <path-to-AlfredProd>/bin/alfred_solver
#

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(SPECPATH)   # AlfredDEV root (where this .spec lives)

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
# PyInstaller's static analyser misses dynamically-loaded modules.
# List every algorithm and shared utility that the solver uses.
hidden = [
    # pyarrow (Parquet serde)
    "pyarrow",
    "pyarrow.parquet",
    "pyarrow.lib",
    "pyarrow._parquet",

    # Solver & algorithms
    "alfred.optimization.solver",
    "alfred.optimization.algorithms.registry",
    "alfred.optimization.algorithms.offline.algorithm",
    "alfred.optimization.algorithms.offline.offline_algorithms",
    "alfred.optimization.algorithms.offline.pipeline",
    "alfred.optimization.algorithms.insert.algorithm",
    "alfred.optimization.algorithms.insert.insert_algorithms",
    "alfred.optimization.algorithms.react.algorithm",
    "alfred.optimization.algorithms.react.react_algorithms",
    "alfred.optimization.algorithms.alfred.algorithm",

    # Common utilities
    "alfred.optimization.common.distance_utils",
    "alfred.optimization.common.movements",
    "alfred.optimization.common.preassigned",
    "alfred.optimization.common.utils",

    # Settings
    "alfred.optimization.settings.solver_settings",
    "alfred.optimization.settings.model_params",
    "alfred.optimization.settings.master_data",

    # Data loading (master data is loaded by the solver at runtime)
    "alfred.data.loading.master_data_loader",
    "alfred.data.id_normalization",

    # Utils
    "alfred.utils.datetime_utils",
    "alfred.utils.logging_utils",

    # Serde module
    "solver_executable.serde",

    # License check module
    "solver_executable.license_check",
    "cryptography",
    "cryptography.hazmat.bindings._rust",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "cryptography.hazmat.primitives.serialization",

    # Standard library modules sometimes missed
    "multiprocessing.pool",
    "multiprocessing.managers",
]

# ---------------------------------------------------------------------------
# Data files (non-Python resources bundled into the binary)
# ---------------------------------------------------------------------------
# We do NOT bundle master data (directorio, duraciones, dist_dict) — those are
# passed in at runtime via the temp-dir IPC mechanism.
datas = []

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(ROOT / "src" / "solver_executable" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # GUI / notebook / web frameworks not needed in the binary
        "tkinter",
        "matplotlib",
        "IPython",
        "jupyter",
        "notebook",
        "flask",
        "fastapi",
        "uvicorn",
    ],
    noarchive=False,
)

# ---------------------------------------------------------------------------
# PYZ archive
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# Executable
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="alfred_solver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX compression can break some libraries; keep off
    console=True,       # CLI tool — keep the console window
    onefile=True,
)
