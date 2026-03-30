"""
solver_bridge.py — Drop-in replacement for OptimizationSolver in AlfredProd.

This module is **only used in AlfredProd** (never in AlfredDEV, which has the
real solver).  It exposes the identical public interface as OptimizationSolver:

    solver = SolverBridge(input_df, settings, context, master_data_override)
    results_df, metrics, algo_artifacts = solver.solve()

Internally it:
  1. Serializes all inputs to a temp directory via serde.py.
  2. Launches the alfred_solver binary (--mode solve) as a subprocess.
  3. Waits for completion (blocking).
  4. Deserializes and returns the output 3-tuple.
  5. Cleans up the temp directory unconditionally.

Configuration (via environment variables):
    ALFRED_SOLVER_BIN     Path to the alfred_solver binary.
                          Default: bin/alfred_solver (relative to the repo root,
                          resolved from this file's location).
    ALFRED_SOLVER_TIMEOUT Subprocess timeout in seconds.  Default: 600.
    ALFRED_LICENSE        Path to the license file issued for this deployment.
                          Required — the binary will exit with code 5 if absent.

Error handling:
    Any subprocess failure (non-zero exit code, timeout, or missing output)
    raises SolverBridgeError.  One automatic retry is attempted before raising.
    Exit code 5 (license failure) is never retried and produces a clear message.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _default_binary_path() -> Path:
    """Resolve default binary path: <repo_root>/bin/alfred_solver."""
    # This file lives at src/alfred/optimization/solver_bridge.py, so repo root is 4 levels up.
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    suffix = ".exe" if os.name == "nt" else ""
    return repo_root / "bin" / f"alfred_solver{suffix}"


def _solver_bin() -> Path:
    env_path = os.environ.get("ALFRED_SOLVER_BIN")
    return Path(env_path) if env_path else _default_binary_path()


def _solver_timeout() -> float:
    return float(os.environ.get("ALFRED_SOLVER_TIMEOUT", "600"))


def _license_path() -> Optional[str]:
    return os.environ.get("ALFRED_LICENSE")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SolverBridgeError(RuntimeError):
    """Raised when the alfred_solver binary fails or times out."""


class SolverTimeoutError(SolverBridgeError):
    """Raised when the alfred_solver binary times out (never retried)."""


# ---------------------------------------------------------------------------
# SolverBridge
# ---------------------------------------------------------------------------

class SolverBridge:
    """
    Drop-in replacement for OptimizationSolver that delegates execution to the
    alfred_solver binary via subprocess + temp-dir IPC.

    Public interface is identical to OptimizationSolver:
        __init__(input_df, settings, context=None, master_data_override=None)
        solve() -> (results_df, metrics, algo_artifacts)
    """

    SOLVER_VERSION = "0.2.0"  # Keep in sync with OptimizationSolver.SOLVER_VERSION

    def __init__(
        self,
        input_df: pd.DataFrame,
        settings,
        context: Optional[Dict[str, Any]] = None,
        master_data_override=None,
    ) -> None:
        self.input_df = input_df.copy()
        self.settings = settings
        self.context = context or {}
        self.master_data = master_data_override
        # master_data_override must be provided — the bridge cannot call OSRM
        if self.master_data is None:
            raise SolverBridgeError(
                "SolverBridge requires master_data_override — pass the MasterData object "
                "explicitly (the bridge cannot load master data independently)."
            )

    def solve(self) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
        """
        Run the solver executable and return (results_df, metrics, algo_artifacts).
        One retry is attempted on failure before raising SolverBridgeError.
        Timeouts (SolverTimeoutError) are never retried.
        """
        tmpdir = Path(tempfile.mkdtemp(prefix="alfred_solver_"))
        try:
            return self._run_with_retry(tmpdir)
        finally:
            try:
                shutil.rmtree(tmpdir)
            except Exception as exc:
                logger.warning("solver_bridge: failed to clean tmpdir %s: %s", tmpdir, exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_with_retry(
        self,
        tmpdir: Path,
    ) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
        try:
            return self._run_once(tmpdir, attempt=1)
        except SolverTimeoutError:
            raise  # never retry a timeout — the binary is hung, not transiently failing
        except SolverBridgeError as exc:
            logger.warning("solver_bridge: attempt 1 failed (%s) — retrying", exc)
        return self._run_once(tmpdir, attempt=2)

    def _run_once(
        self,
        tmpdir: Path,
        attempt: int,
    ) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
        from alfred.optimization.solver_serde import (
            serialize_solver_input,
            deserialize_solver_output,
        )

        solver_bin = _solver_bin()
        timeout = _solver_timeout()

        if not solver_bin.exists():
            raise SolverBridgeError(
                f"alfred_solver binary not found at {solver_bin}. "
                "Set ALFRED_SOLVER_BIN or install the binary to bin/alfred_solver."
            )

        logger.info(
            "solver_bridge: attempt=%d serializing input (rows=%d)",
            attempt,
            len(self.input_df),
        )

        input_json = serialize_solver_input(
            tmpdir,
            self.input_df,
            self.settings,
            self.context,
            self.master_data,
        )
        log_file = tmpdir / "solver.log"

        dev_mode = os.environ.get("ALFRED_DEV_MODE", "").strip() not in ("", "0")
        license = _license_path()
        if not dev_mode:
            if not license:
                raise SolverBridgeError(
                    "No license file provided. Set the ALFRED_LICENSE environment variable "
                    "to the path of the license file issued for this deployment."
                )
            if not Path(license).exists():
                raise SolverBridgeError(
                    f"License file not found: {license}. "
                    "Place the issued license file at this path before running."
                )

        cmd = [
            str(solver_bin),
            "--mode", "solve",
            str(input_json),
            str(tmpdir),
            "--log-file", str(log_file),
            "--log-level", "INFO",
        ]
        if license:
            cmd += ["--license", license]

        logger.info("solver_bridge: launching %s", " ".join(cmd[:4]))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise SolverTimeoutError(
                f"alfred_solver timed out after {timeout}s (attempt {attempt}). "
                "Consider increasing ALFRED_SOLVER_TIMEOUT."
            )

        # Forward stderr lines — debug on success, error on failure
        if proc.stderr.strip():
            lines = proc.stderr.strip().splitlines()
            log_fn = logger.debug if proc.returncode == 0 else logger.error
            for line in lines[:20]:
                log_fn("solver_stderr | %s", line)
            if len(lines) > 20:
                log_fn("solver_stderr | ... (%d more lines suppressed)", len(lines) - 20)

        if proc.returncode == 5:
            raise SolverBridgeError(
                f"alfred_solver rejected the license file at '{license}'. "
                "Check that the file exists, is not expired, and was issued for this deployment."
            )
        if proc.returncode != 0:
            raise SolverBridgeError(
                f"alfred_solver exited with code {proc.returncode} (attempt {attempt})"
            )

        output_json_str = proc.stdout.strip()
        if not output_json_str:
            raise SolverBridgeError(
                f"alfred_solver produced no output path on stdout (attempt {attempt})"
            )

        output_json = Path(output_json_str)
        try:
            output_json.relative_to(tmpdir)
        except ValueError:
            raise SolverBridgeError(
                f"alfred_solver reported output path outside tmpdir: {output_json} "
                f"(expected inside {tmpdir}, attempt {attempt})"
            )
        if not output_json.exists():
            raise SolverBridgeError(
                f"alfred_solver reported output path {output_json} but it does not exist "
                f"(attempt {attempt})"
            )

        logger.info("solver_bridge: deserializing output from %s", output_json)
        results_df, metrics, algo_artifacts = deserialize_solver_output(output_json)

        logger.info(
            "solver_bridge: done rows=%d attempt=%d",
            len(results_df),
            attempt,
        )
        return results_df, metrics, algo_artifacts
