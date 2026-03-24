"""
Pipeline Profiling Grid-Search Experiment
==========================================

Runs a grid of (algorithm x max_iterations) cells against the same input data,
tracking F.O (total_driver_move_distance_km) and per-stage wall times.

Grid:
  Algorithms:    OFFLINE, INSERT, REACT
  max_iterations: 100, 500, 1000, 2000, 5000
  Fixed:         distance_method=osrm, time_method=osrm_times, n_processes=-1

NOTE on REACT: run with context={}, so preassigned={} — all labors are
treated as new (no frozen schedule). This is a valid benchmarking mode; REACT
degrades gracefully to near-OFFLINE in this case.

NOTE on reproducibility: alpha=0.3 (GRASP randomness). Results may vary slightly
across runs due to multiprocessing scheduling. Fix PYTHONHASHSEED=0 in the shell
for maximum reproducibility.

Usage:
    micromamba run -n AlfredEnv python scripts/profiling_grid_search.py

Outputs (under data/profiling/):
    grid_search_<timestamp>.csv
    grid_search_<timestamp>.json
"""

import itertools
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Project root on sys.path (works whether run from project root or scripts/)
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Suppress noisy sub-loggers during the grid run
logging.getLogger("src.optimization.algorithms").setLevel(logging.WARNING)
logging.getLogger("src.optimization.solver").setLevel(logging.WARNING)
logging.getLogger("src.geo").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Imports (after sys.path is set)
# ---------------------------------------------------------------------------
from alfred.config import Config
from alfred.data.loading.driver_directory_loader import load_driver_directory_df
from alfred.data.loading.master_data_loader import MasterData, load_master_data
from alfred.data.parsing.driver_directory_parser import DriverDirectoryParser
from alfred.data.parsing.input_parser import InputParser
from alfred.data.validation.validator import InputValidator
from alfred.data.validation.rules.generic import RequiredFieldRule, NonEmptyRowRule, UniqueLaborIdRule
from alfred.data.validation.rules.domain import (
    CreatedBeforeScheduleRule,
    ValidDepartmentsOnly,
    ValidLocationResolutionStatus,
)
from alfred.data.api.client import ALFREDAPIClient
from alfred.data.io.input_loader import load_local_input
from alfred.data.io.request_loader import apply_request_filters, load_request
from alfred.optimization.evaluation.solution_evaluator import evaluate_solution
from alfred.optimization.settings.solver_settings import OptimizationSettings
from alfred.optimization.solver import OptimizationSolver
from alfred.pipeline.filters import (
    _filter_df_by_department_code,
    _filter_labors_to_planning_window,
    filter_canceled_services,
)

# ---------------------------------------------------------------------------
# Grid configuration
# ---------------------------------------------------------------------------
ALGORITHMS = ["OFFLINE", "INSERT", "REACT"]
MAX_ITERATIONS_GRID = [100, 500, 1000, 2000, 5000]
DEPT_CODES = ["25", "5", "76", "8", "13", "68", "66"]

OUTPUT_DIR = _PROJECT_ROOT / "data" / "profiling"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_validation_rules() -> list:
    """Replicates main.py validation rules (lines 313-337)."""
    return [
        NonEmptyRowRule(),
        RequiredFieldRule("service_id"),
        RequiredFieldRule("labor_id"),
        RequiredFieldRule("created_at"),
        RequiredFieldRule("schedule_date"),
        RequiredFieldRule("start_address_point"),
        RequiredFieldRule("labor_name"),
        RequiredFieldRule("end_address_point"),
        UniqueLaborIdRule(),
        CreatedBeforeScheduleRule(minimum_delta_hours=2.0),
        ValidDepartmentsOnly(
            field="department_code",
            valid_departments=("25", "76", "5"),
        ),
        ValidLocationResolutionStatus(
            field="location_resolution_status",
            valid_statuses=("resolved", "resolved_department_only"),
        ),
    ]


def build_grid_settings(algo: str, max_iter: int) -> OptimizationSettings:
    """Construct a fresh OptimizationSettings for a single grid cell."""
    iter_dict = {dept: max_iter for dept in DEPT_CODES}
    return OptimizationSettings(
        algorithm=algo,
        distance_method="osrm",
        time_method="osrm_times",
        n_processes=-1,
        max_iterations={algo: iter_dict},
    )


def load_shared_data() -> tuple[pd.DataFrame, MasterData, dict[str, float]]:
    """
    Load and validate input once, to be reused for all grid cells.

    Branches on Config.USE_API:
      - True:  fetches optimization data + driver directory from live API
               using filters from request.json (department, dates)
      - False: loads from local files as configured in .env

    Returns:
        valid_df:     validated input DataFrame
        master_data:  MasterData with driver directory merged in
        stage_times:  dict with fetch_s, parse_s, validate_s (constant across grid)
    """
    Config.validate()  # parses START_DATE, END_DATE, DEPARTMENT from env vars

    # --- Load request.json for filters ---
    request_path = os.getenv("REQUEST_PATH", "request.json")
    request_payload = load_request(request_path)
    filters = request_payload.filters

    department = filters.department if filters and filters.department is not None else Config.DEPARTMENT
    start_date = filters.start_date if filters and filters.start_date is not None else Config.START_DATE
    end_date = filters.end_date if filters and filters.end_date is not None else Config.END_DATE
    schedule_date = filters.schedule_date if filters else None

    # --- Fetch optimization data ---
    if Config.USE_API:
        logger.info("Fetching optimization data from API (department=%s)", department)
        t_fetch = time.perf_counter()
        client = ALFREDAPIClient(
            endpoint_url=Config.SERVICES_ENDPOINT,
            api_token=Config.API_TOKEN,
            timeout=Config.REQUEST_TIMEOUT,
            max_retries=Config.API_MAX_RETRIES,
        )
        raw_input = client.get_optimization_data(
            department=department,
            start_date=start_date,
            end_date=end_date,
        )
        fetch_s = time.perf_counter() - t_fetch
        logger.info("API fetch done (%.2fs)", fetch_s)
    else:
        logger.info("Loading local input from %s", Config.LOCAL_INPUT_PATH)
        raw_input = load_local_input(Config.LOCAL_INPUT_PATH)
        fetch_s = 0.0

    # --- Parse ---
    t0 = time.perf_counter()
    input_df, _ = InputParser.parse(raw_input)
    parse_s = time.perf_counter() - t0
    logger.info("Parsed: %d rows (%.2fs)", len(input_df), parse_s)

    # --- Apply request filters (date window, canceled services) ---
    if filters:
        input_df = _filter_labors_to_planning_window(input_df, filters)
    input_df = filter_canceled_services(input_df)
    if not Config.USE_API and filters:
        input_df = apply_request_filters(input_df, filters)

    # --- Validate ---
    t0 = time.perf_counter()
    validator = InputValidator(rules=_build_validation_rules())
    valid_df, invalid_df, _ = validator.validate(input_df)
    validate_s = time.perf_counter() - t0
    logger.info(
        "Validated: %d valid / %d invalid rows (%.2fs)",
        len(valid_df), len(invalid_df), validate_s,
    )

    if valid_df.empty:
        raise RuntimeError("All rows failed validation — cannot run grid.")

    # --- Driver directory ---
    if Config.USE_API:
        if schedule_date is None and not valid_df.empty and "schedule_date" in valid_df.columns:
            schedule_date = valid_df["schedule_date"].dt.date.iloc[0]
        driver_client = ALFREDAPIClient(
            endpoint_url=Config.ALFREDS_ENDPOINT,
            api_token=Config.API_TOKEN,
            timeout=Config.REQUEST_TIMEOUT,
            max_retries=Config.API_MAX_RETRIES,
        )
        raw_drivers = driver_client.get_driver_directory(
            active=True,
            schedule_date=schedule_date,
            department=department,
        )
        driver_directory_df = DriverDirectoryParser.parse(raw_drivers)
        logger.info("Loaded %d drivers from API", len(driver_directory_df))
    else:
        driver_directory_df = load_driver_directory_df(Config.LOCAL_DRIVER_DIRECTORY_FILE)
        if department is not None:
            driver_directory_df = _filter_df_by_department_code(
                driver_directory_df,
                department=department,
                dataset_name="driver_directory",
            )
        logger.info("Loaded %d drivers from local file", len(driver_directory_df))

    if driver_directory_df.empty:
        raise RuntimeError("Driver directory is empty — cannot run grid.")

    # --- Master data (lru_cache warms on first call) ---
    base_master = load_master_data(OptimizationSettings().master_data)
    master_data = MasterData(
        directorio_df=driver_directory_df,
        duraciones_df=base_master.duraciones_df,
        dist_dict=base_master.dist_dict,
    )

    stage_times = {
        "fetch_s": round(fetch_s, 4),
        "parse_s": round(parse_s, 4),
        "validate_s": round(validate_s, 4),
    }
    return valid_df, master_data, stage_times


def run_one_cell(
    algo: str,
    max_iter: int,
    valid_df: pd.DataFrame,
    master_data: MasterData,
) -> dict[str, Any]:
    """
    Run one grid cell: solve + evaluate, return a flat metrics dict.

    NOTE: valid_df must be a .copy() — _prepare_data() mutates it in-place.
    """
    settings = build_grid_settings(algo, max_iter)
    wall_t0 = time.perf_counter()

    # --- Solve ---
    t0 = time.perf_counter()
    solver = OptimizationSolver(
        valid_df,
        settings=settings,
        context={},
        master_data_override=master_data,
    )
    results_df, solver_metrics, algo_artifacts = solver.solve()
    solve_s = time.perf_counter() - t0

    # --- Evaluate ---
    moves_df = algo_artifacts.get("moves_df", pd.DataFrame()) if isinstance(algo_artifacts, dict) else pd.DataFrame()
    t0 = time.perf_counter()
    _, evaluation = evaluate_solution(
        labors_df=results_df,
        moves_df=moves_df,
        driver_directory_df=master_data.directorio_df,
    )
    evaluate_s = time.perf_counter() - t0

    total_s = time.perf_counter() - wall_t0

    summary = evaluation.get("summary", {})
    algo_metrics = solver_metrics.get("algorithm_metrics", {})

    return {
        "algorithm": algo,
        "max_iter": max_iter,
        # F.O and assignment quality
        "driver_move_distance_km": summary.get("total_driver_move_distance_km"),
        "total_labor_distance_km": summary.get("total_labor_distance_km"),
        "vt_labors_assigned": summary.get("vt_labors_assigned"),
        "services_successfully_assigned": summary.get("services_successfully_assigned"),
        "services_total": summary.get("services_total"),
        "utilization_without_moves_pct": summary.get("utilization_without_moves_pct"),
        # Timing
        "algo_elapsed_s": algo_metrics.get("elapsed_seconds"),
        "solver_wall_s": solver_metrics.get("execution_time_seconds"),
        "solve_s": round(solve_s, 4),
        "evaluate_s": round(evaluate_s, 4),
        "total_s": round(total_s, 4),
        # Metadata
        "run_id": solver_metrics.get("run_id"),
        "error": None,
    }


def main() -> None:
    logger.info("=== Pipeline Profiling Grid-Search ===")
    logger.info("Grid: algorithms=%s  max_iterations=%s", ALGORITHMS, MAX_ITERATIONS_GRID)

    # OSRM availability guard
    osrm_url = os.getenv("OSRM_URL", "")
    if not osrm_url:
        logger.warning(
            "OSRM_URL is not set — runs will fall back to haversine distances. "
            "F.O values will NOT be comparable to production. Set OSRM_URL in .env."
        )
    else:
        logger.info("OSRM_URL=%s", osrm_url)

    # --- Load data once ---
    valid_df, master_data, shared_stage_times = load_shared_data()
    logger.info("Shared stage times: %s", shared_stage_times)

    grid = list(itertools.product(ALGORITHMS, MAX_ITERATIONS_GRID))
    logger.info("Running %d grid cells...", len(grid))

    results = []
    for i, (algo, max_iter) in enumerate(grid, 1):
        label = f"[{i}/{len(grid)}] {algo} / max_iter={max_iter}"
        logger.info("%s — starting", label)
        try:
            row = run_one_cell(algo, max_iter, valid_df.copy(), master_data)
            logger.info(
                "%s — done  driver_move_km=%.2f  algo_s=%.1f  total_s=%.1f",
                label,
                row.get("driver_move_distance_km") or 0,
                row.get("algo_elapsed_s") or 0,
                row.get("total_s") or 0,
            )
        except Exception as exc:
            logger.exception("%s — FAILED: %s", label, exc)
            row = {
                "algorithm": algo,
                "max_iter": max_iter,
                "error": str(exc),
            }
        row.update(shared_stage_times)
        results.append(row)

    # --- Save ---
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    df_results = pd.DataFrame(results)

    csv_path = OUTPUT_DIR / f"grid_search_{timestamp}.csv"
    json_path = OUTPUT_DIR / f"grid_search_{timestamp}.json"

    df_results.to_csv(csv_path, index=False)
    df_results.to_json(json_path, orient="records", indent=2)

    logger.info("Results saved to %s", csv_path)
    logger.info("Results saved to %s", json_path)

    # Quick summary
    ok = df_results[df_results["error"].isna()]
    if not ok.empty:
        pivot = ok.pivot_table(
            index="algorithm",
            columns="max_iter",
            values="driver_move_distance_km",
        )
        print("\n=== F.O Summary (driver_move_distance_km) ===")
        print(pivot.to_string())

        pivot_t = ok.pivot_table(
            index="algorithm",
            columns="max_iter",
            values="algo_elapsed_s",
        )
        print("\n=== Algo Elapsed Time (seconds) ===")
        print(pivot_t.to_string())


if __name__ == "__main__":
    main()
