"""
main.py — Entry point for the alfred_solver executable.

This module is bundled by PyInstaller into a self-contained binary.
It dispatches to one of three modes based on the --mode flag:

  --mode solve   Full optimization via OptimizationSolver.
  --mode probe   Single insertion-feasibility check via run_insertion_worker.
  --mode health  Self-test: verify license, serde, and solver modules load.

Usage:
    alfred_solver --mode solve  <input_json> <output_dir> [--license <path>] [--log-file <path>] [--log-level LEVEL]
    alfred_solver --mode probe  <input_json> <output_dir> [--license <path>] [--log-file <path>] [--log-level LEVEL]
    alfred_solver --mode health                           [--license <path>] [--log-file <path>] [--log-level LEVEL]

    The license file path can also be supplied via the ALFRED_LICENSE environment variable.
    Set ALFRED_DEV_MODE=1 to skip license validation (DEV builds only — never in production).

Exit codes:
    0  Success — output JSON path printed to stdout (solve/probe), or health JSON printed (health).
    1  Bad arguments.
    2  Deserialization error.
    3  Solver / probe execution error.
    4  Output serialization error.
    5  License invalid or expired.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# freeze_support MUST be the very first call after __name__ guard
# (required for multiprocessing.Pool inside a PyInstaller onefile bundle)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    multiprocessing.freeze_support()


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _configure_logging(log_level: str, log_file: Path | None) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        handlers=handlers,
        force=True,
    )


logger = logging.getLogger("alfred_solver")


# ---------------------------------------------------------------------------
# Mode: solve
# ---------------------------------------------------------------------------

def _run_solve(input_json: Path, output_dir: Path) -> Path:
    """
    Deserialize inputs, run OptimizationSolver, serialize outputs.
    Returns path to solver_output.json.
    """
    from solver_executable.serde import deserialize_solver_input, serialize_solver_output

    logger.info("solve: deserializing input from %s", input_json)
    try:
        input_df, settings, context, master_data = deserialize_solver_input(input_json)
    except Exception as exc:
        logger.exception("solve: deserialization failed")
        raise _DeserializeError(str(exc)) from exc

    logger.info(
        "solve: input_df rows=%d algorithm=%s",
        len(input_df),
        settings.algorithm,
    )

    _t0 = time.time()
    try:
        from alfred.optimization.solver import OptimizationSolver
        solver = OptimizationSolver(
            input_df=input_df,
            settings=settings,
            context=context,
            master_data_override=master_data,
        )
        results_df, metrics, algo_artifacts = solver.solve()
    except Exception as exc:
        logger.exception("solve: solver execution failed")
        raise _SolverError(str(exc)) from exc

    logger.info(
        "solve: completed rows=%d infeasible=%d elapsed=%.1fs",
        len(results_df),
        int(results_df.get("is_infeasible", False).sum()) if "is_infeasible" in results_df.columns else -1,
        time.time() - _t0,
    )

    try:
        output_json = serialize_solver_output(output_dir, results_df, metrics, algo_artifacts)
    except Exception as exc:
        logger.exception("solve: output serialization failed")
        raise _SerializeError(str(exc)) from exc

    return output_json


# ---------------------------------------------------------------------------
# Mode: probe
# ---------------------------------------------------------------------------

def _run_probe(input_json: Path, output_dir: Path) -> Path:
    """
    Deserialize probe inputs, run run_insertion_worker, serialize output.
    Returns path to probe_output.json.
    """
    from solver_executable.serde import deserialize_probe_input, serialize_probe_output

    logger.info("probe: deserializing input from %s", input_json)
    try:
        kwargs = deserialize_probe_input(input_json)
    except Exception as exc:
        logger.exception("probe: deserialization failed")
        raise _DeserializeError(str(exc)) from exc

    logger.info(
        "probe: city=%s fecha=%s seed=%s candidate_rows=%d",
        kwargs.get("city"),
        kwargs.get("fecha"),
        kwargs.get("seed"),
        len(kwargs.get("new_labors_df", [])),
    )

    try:
        from alfred.optimization.algorithms.insert.insert_algorithms import run_insertion_worker
        result = run_insertion_worker(**kwargs)
    except Exception as exc:
        logger.exception("probe: run_insertion_worker failed")
        raise _SolverError(str(exc)) from exc

    logger.info("probe: num_inserted=%d", result.get("num_inserted", 0))

    try:
        output_json = serialize_probe_output(output_dir, result)
    except Exception as exc:
        logger.exception("probe: output serialization failed")
        raise _SerializeError(str(exc)) from exc

    return output_json


# ---------------------------------------------------------------------------
# Mode: health
# ---------------------------------------------------------------------------

def _run_health() -> None:
    """
    Minimal self-test: verify serde and solver modules load correctly.
    License has already been validated by the caller.
    Prints a JSON status line to stdout.
    """
    import json as _json
    from solver_executable.serde import SERDE_VERSION
    from alfred.optimization.solver import OptimizationSolver
    status = {
        "status": "ok",
        "serde_version": SERDE_VERSION,
        "solver_version": getattr(OptimizationSolver, "SOLVER_VERSION", "unknown"),
    }
    print(_json.dumps(status), flush=True)


# ---------------------------------------------------------------------------
# Internal exception hierarchy (used for exit code mapping)
# ---------------------------------------------------------------------------

class _DeserializeError(RuntimeError): ...
class _SolverError(RuntimeError): ...
class _SerializeError(RuntimeError): ...


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alfred_solver",
        description="Alfred solver executable — solve or probe mode.",
    )
    parser.add_argument(
        "--mode",
        choices=["solve", "probe", "health"],
        required=True,
        help="Execution mode: 'solve', 'probe', or 'health' (self-test, no data needed).",
    )
    parser.add_argument(
        "input_json",
        type=Path,
        nargs="?",
        default=None,
        help="Path to the input JSON envelope produced by serde.serialize_*_input(). Required for solve/probe.",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        nargs="?",
        default=None,
        help="Directory where output files will be written (must exist). Required for solve/probe.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Optional path to a log file (in addition to stderr).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR). Default: INFO.",
    )
    parser.add_argument(
        "--license",
        default=os.environ.get("ALFRED_LICENSE"),
        help="Path to the license file. Can also be set via ALFRED_LICENSE env var.",
    )
    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args()
    except SystemExit:
        return 1

    _configure_logging(args.log_level, args.log_file)

    # License check — must pass before any work is done
    if os.environ.get("ALFRED_DEV_MODE") == "1":
        logger.warning("DEV MODE — license check skipped. Do not use in production.")
    else:
        if not args.license:
            logger.error("No license file provided. Use --license <path> or set ALFRED_LICENSE.")
            return 5
        from solver_executable.license_check import check_license
        check_license(args.license)  # exits with code 5 on failure

    # Health check mode — license already validated; no input/output files needed
    if args.mode == "health":
        _run_health()
        return 0

    input_json: Path = args.input_json
    output_dir: Path = args.output_dir

    if input_json is None or output_dir is None:
        logger.error("input_json and output_dir are required for --mode solve/probe")
        return 1
    if not input_json.exists():
        logger.error("Input file does not exist: %s", input_json)
        return 1
    if not output_dir.is_dir():
        logger.error("Output directory does not exist: %s", output_dir)
        return 1
    if not os.access(output_dir, os.W_OK):
        logger.error("Output directory is not writable: %s", output_dir)
        return 1

    try:
        if args.mode == "solve":
            output_json = _run_solve(input_json, output_dir)
        else:
            output_json = _run_probe(input_json, output_dir)
    except _DeserializeError:
        return 2
    except _SolverError:
        return 3
    except _SerializeError:
        return 4
    except Exception:
        logger.exception("Unexpected error")
        return 1

    # Print the output JSON path to stdout so the bridge can locate it
    print(str(output_json), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
