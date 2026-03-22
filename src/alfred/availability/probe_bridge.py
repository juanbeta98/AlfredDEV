"""
probe_bridge.py — Drop-in replacement for run_insertion_worker in AlfredProd.

This module is **only used in AlfredProd** (never in AlfredDEV, which has the
real insert_algorithms module).  It exposes a function with the identical
signature as run_insertion_worker:

    result = run_insertion_worker_bridge(
        base_labors_df=..., base_moves_df=..., new_labors_df=...,
        seed=0, city=..., fecha=..., directorio_df=..., drivers=...,
        dist_dict=..., distance_method=..., alfred_speed=...,
        vehicle_transport_speed=..., tiempo_alistar=...,
        tiempo_finalizacion=..., tiempo_gracia=..., early_buffer=...,
        workday_end_dt=..., duraciones_df=..., time_method=..., time_dict=...,
    )

In feasibility_probe.py (AlfredProd), the import is swapped to:

    from alfred.availability.probe_bridge import run_insertion_worker_bridge as run_insertion_worker

So probe_slot() and all callers remain completely unchanged.

Configuration (via environment variables):
    ALFRED_SOLVER_BIN     Path to the alfred_solver binary.
                          Default: bin/alfred_solver (relative to repo root).
    ALFRED_PROBE_TIMEOUT  Subprocess timeout in seconds for a single probe.
                          Default: 60 (probes are fast; use a tighter budget).
    ALFRED_LICENSE        Path to the license file issued for this deployment.
                          Required — the binary will exit with code 5 if absent.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _default_binary_path() -> Path:
    """Resolve default binary path: <repo_root>/bin/alfred_solver."""
    # This file lives at src/alfred/availability/probe_bridge.py → repo root is 4 levels up.
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    suffix = ".exe" if os.name == "nt" else ""
    return repo_root / "bin" / f"alfred_solver{suffix}"


def _solver_bin() -> Path:
    env_path = os.environ.get("ALFRED_SOLVER_BIN")
    return Path(env_path) if env_path else _default_binary_path()


def _probe_timeout() -> float:
    return float(os.environ.get("ALFRED_PROBE_TIMEOUT", "60"))


def _license_path() -> Optional[str]:
    return os.environ.get("ALFRED_LICENSE")


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class ProbeBridgeError(RuntimeError):
    """Raised when the alfred_solver binary fails during a probe."""


# ---------------------------------------------------------------------------
# Public function — same signature as run_insertion_worker
# ---------------------------------------------------------------------------

def run_insertion_worker_bridge(
    base_labors_df: pd.DataFrame,
    base_moves_df: pd.DataFrame,
    new_labors_df: pd.DataFrame,
    seed: int,
    city: str,
    fecha: str,
    directorio_df: pd.DataFrame,
    drivers: List[str],
    dist_dict: Dict[Any, Any],
    distance_method: str,
    alfred_speed: float,
    vehicle_transport_speed: float,
    tiempo_alistar: float,
    tiempo_finalizacion: float,
    tiempo_gracia: float,
    early_buffer: float,
    workday_end_dt,
    duraciones_df: Optional[pd.DataFrame] = None,
    time_method: str = "speed_based",
    time_dict: Optional[Dict[Any, Any]] = None,
) -> Dict[str, Any]:
    """
    Delegate a single insertion-feasibility probe to the alfred_solver binary.

    Identical signature to run_insertion_worker from insert_algorithms.py.
    Returns the same result dict: {valid, seed, num_inserted, dist, results, moves}.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="alfred_probe_"))
    try:
        return _run_probe(
            tmpdir=tmpdir,
            base_labors_df=base_labors_df,
            base_moves_df=base_moves_df,
            new_labors_df=new_labors_df,
            seed=seed,
            city=city,
            fecha=fecha,
            directorio_df=directorio_df,
            drivers=drivers,
            dist_dict=dist_dict,
            distance_method=distance_method,
            alfred_speed=alfred_speed,
            vehicle_transport_speed=vehicle_transport_speed,
            tiempo_alistar=tiempo_alistar,
            tiempo_finalizacion=tiempo_finalizacion,
            tiempo_gracia=tiempo_gracia,
            early_buffer=early_buffer,
            workday_end_dt=workday_end_dt,
            duraciones_df=duraciones_df,
            time_method=time_method,
            time_dict=time_dict,
        )
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception as exc:
            logger.warning("probe_bridge: failed to clean tmpdir %s: %s", tmpdir, exc)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _run_probe(tmpdir: Path, **kwargs) -> Dict[str, Any]:
    from alfred.optimization.solver_serde import serialize_probe_input, deserialize_probe_output

    solver_bin = _solver_bin()
    timeout = _probe_timeout()

    if not solver_bin.exists():
        raise ProbeBridgeError(
            f"alfred_solver binary not found at {solver_bin}. "
            "Set ALFRED_SOLVER_BIN or install the binary to bin/alfred_solver."
        )

    license = _license_path()
    if not license:
        raise ProbeBridgeError(
            "No license file provided. Set the ALFRED_LICENSE environment variable "
            "to the path of the license file issued for this deployment."
        )
    if not Path(license).exists():
        raise ProbeBridgeError(
            f"License file not found: {license}. "
            "Place the issued license file at this path before running."
        )

    input_json = serialize_probe_input(tmpdir, **kwargs)
    log_file = tmpdir / "probe.log"

    cmd = [
        str(solver_bin),
        "--mode", "probe",
        str(input_json),
        str(tmpdir),
        "--license", license,
        "--log-file", str(log_file),
        "--log-level", "WARNING",   # probes are noisy; only log warnings+
    ]

    logger.debug(
        "probe_bridge: launching probe city=%s fecha=%s seed=%s",
        kwargs.get("city"),
        kwargs.get("fecha"),
        kwargs.get("seed"),
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ProbeBridgeError(
            f"alfred_solver probe timed out after {timeout}s "
            f"(city={kwargs.get('city')}, fecha={kwargs.get('fecha')})"
        )

    # Forward stderr lines at warning level (capped to avoid log flooding)
    if proc.stderr.strip():
        lines = proc.stderr.strip().splitlines()
        for line in lines[:20]:
            logger.warning("probe_stderr | %s", line)
        if len(lines) > 20:
            logger.warning("probe_stderr | ... (%d more lines suppressed)", len(lines) - 20)

    if proc.returncode == 5:
        raise ProbeBridgeError(
            f"alfred_solver rejected the license file at '{license}'. "
            "Check that the file exists, is not expired, and was issued for this deployment."
        )
    if proc.returncode != 0:
        raise ProbeBridgeError(
            f"alfred_solver probe exited with code {proc.returncode} "
            f"(city={kwargs.get('city')}, fecha={kwargs.get('fecha')})"
        )

    output_json_str = proc.stdout.strip()
    if not output_json_str:
        raise ProbeBridgeError("alfred_solver probe produced no output path on stdout")

    output_json = Path(output_json_str)
    if not output_json.exists():
        raise ProbeBridgeError(
            f"alfred_solver probe reported output path {output_json} but it does not exist"
        )

    result = deserialize_probe_output(output_json)

    logger.debug(
        "probe_bridge: done num_inserted=%d city=%s",
        result.get("num_inserted", 0),
        kwargs.get("city"),
    )
    return result
