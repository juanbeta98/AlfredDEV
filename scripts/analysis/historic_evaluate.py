"""
Reconstruct historical schedules and evaluate KPIs for a fetched period.

For each day in data/historic_analysis/<period-name>/, loads the API snapshots,
reconstructs the preassigned schedule (mirroring orchestrator steps 4, 6, 7, 10),
evaluates KPIs, and writes a daily_kpis.csv.

Requires OSRM_URL to be set (OSRM is used for distance computation).

Usage (from repo root):
    scripts/mrun python scripts/analysis/historic_evaluate.py --period-name before
    scripts/mrun python scripts/analysis/historic_evaluate.py --period-name before --dry-run
    scripts/mrun python scripts/analysis/historic_evaluate.py --period-name before --grace-minutes 20
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

def _find_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "data").exists() and (candidate / "src").exists():
            return candidate
    raise RuntimeError("Could not resolve repository root from script path.")


ROOT = _find_repo_root()
sys.path.insert(0, str(ROOT / "src"))

logger = logging.getLogger(__name__)

HISTORIC_DIR = ROOT / "data" / "historic_analysis"


# ---------------------------------------------------------------------------
# OSRM pre-computation (mirrors orchestrator lines 588-647)
# ---------------------------------------------------------------------------

def _precompute_osrm_distances(
    labors_df,
    driver_directory_df,
    master_data,
    osrm_url: str,
) -> tuple[dict, dict]:
    """
    Call the OSRM Table API once per department to pre-fill dist_dict and
    time_dict, avoiding per-route OSRM calls during reconstruct_preassigned_state.

    Both dicts are passed to reconstruct_preassigned_state so that
    compute_driver_move() uses the walk-buffer + transit-slowdown model with
    pre-fetched OSRM durations rather than falling back to constant speed.

    Returns (dist_dict, time_dict).
    """
    import pandas as pd
    from alfred.optimization.common.distance_utils import batch_distance_matrix, _is_valid_coord
    from alfred.optimization.common.movements import _filter_drivers_by_city

    dist_dict: Dict[str, Any] = {k: dict(v) for k, v in master_data.dist_dict.items()}
    time_dict: Dict[Any, Any] = {}

    department_codes = (
        labors_df["department_code"].dropna().unique().tolist()
        if "department_code" in labors_df.columns
        else []
    )

    for dept in department_codes:
        dept_str = str(dept)
        city_dir = _filter_drivers_by_city(driver_directory_df, dept_str)
        driver_pts = [
            f"POINT ({row.longitud} {row.latitud})"
            for _, row in city_dir.iterrows()
            if pd.notna(row.get("latitud")) and pd.notna(row.get("longitud"))
            and _is_valid_coord(float(row.get("longitud")), float(row.get("latitud")))
        ]

        dept_rows = labors_df[labors_df["department_code"].astype(str) == dept_str]
        start_col = "start_address_point" if "start_address_point" in dept_rows.columns else "map_start_point"
        end_col = "end_address_point" if "end_address_point" in dept_rows.columns else "map_end_point"
        starts = dept_rows[start_col].dropna().unique().tolist() if start_col in dept_rows.columns else []
        ends = dept_rows[end_col].dropna().unique().tolist() if end_col in dept_rows.columns else []

        all_pts = list(dict.fromkeys(driver_pts + starts + ends))
        if len(all_pts) < 2:
            continue

        # include_times=True captures the OSRM duration matrix so that
        # compute_driver_move() can apply the walk-buffer + transit-slowdown
        # model with real OSRM times instead of falling back to constant speed.
        batch_dist, batch_time = batch_distance_matrix(all_pts, all_pts, osrm_url, include_times=True)
        if batch_dist:
            existing = dist_dict.get(dept_str, {})
            dist_dict[dept_str] = {**batch_dist, **existing}
            time_dict.update(batch_time)
            logger.info(
                "osrm_precompute dept=%s points=%d pairs=%d time_pairs=%d",
                dept_str, len(all_pts), len(batch_dist), len(batch_time),
            )
        else:
            logger.warning("osrm_precompute_failed dept=%s — reconstruction will use per-call fallback", dept_str)

    return dist_dict, time_dict


# ---------------------------------------------------------------------------
# KPI row extraction
# ---------------------------------------------------------------------------

def _extract_kpi_row(
    day: date,
    period_name: str,
    department: Optional[int],
    eval_report: Dict[str, Any],
    recon_metrics: Dict[str, Any],
    excluded_labors_count: int = 0,
) -> Dict[str, Any]:
    summary = eval_report.get("summary", {})
    punctuality = eval_report.get("punctuality", {})

    # punctuality may be keyed by grace_minutes at the top level
    if punctuality and not any(k in punctuality for k in ("late_services_count", "late_services_pct")):
        # unwrap first sub-dict (keyed by grace window label)
        punctuality = next(iter(punctuality.values()), {})

    return {
        "date": day.isoformat(),
        "period": period_name,
        "department": department,
        # Assignment
        "services_total": summary.get("services_total", 0),
        "labors_total": summary.get("labors_total", 0),
        "vt_labors_total": summary.get("vt_labors_total", 0),
        "vt_labors_assigned": summary.get("vt_labors_assigned", 0),
        "vt_labors_unassigned": summary.get("vt_labors_unassigned", 0),
        "drivers_used": summary.get("drivers_used", 0),
        "failed_services_total": summary.get("failed_services_total", 0),
        # Distance
        "total_labor_distance_km": summary.get("total_labor_distance_km", 0.0),
        "total_driver_move_distance_km": summary.get("total_driver_move_distance_km", 0.0),
        # Utilization
        "utilization_without_moves_pct": summary.get("utilization_without_moves_pct", 0.0),
        "utilization_with_moves_pct": summary.get("utilization_with_moves_pct", 0.0),
        "driver_move_utilization_pct": summary.get("driver_move_utilization_pct", 0.0),
        # Punctuality
        "late_services_count": punctuality.get("late_services_count", 0),
        "late_services_pct": punctuality.get("late_services_pct", 0.0),
        "normalized_tardiness_pct": punctuality.get("normalized_tardiness_pct", 0.0),
        "total_lateness_min": punctuality.get("total_lateness_min", 0.0),
        # Reconstruction infeasibilities
        "preassigned_total": recon_metrics.get("preassigned_total", 0),
        "preassigned_failed": recon_metrics.get("preassigned_failed", 0),
        "preassigned_infeasible": recon_metrics.get("preassigned_infeasible", 0),
        "preassigned_overtime": recon_metrics.get("preassigned_overtime", 0),
        # Excluded outliers
        "excluded_labors_count": excluded_labors_count,
    }


def _zero_kpi_row(day: date, period_name: str, department: Optional[int], reason: str, excluded_labors_count: int = 0) -> Dict[str, Any]:
    return _extract_kpi_row(day, period_name, department, {}, {}, excluded_labors_count) | {"skip_reason": reason}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconstruct and evaluate historical schedules for a fetched period."
    )
    parser.add_argument("--period-name", required=True, help="Period name matching a folder in data/historic_analysis/.")
    parser.add_argument("--grace-minutes", type=int, default=15, help="Grace window in minutes for punctuality KPIs.")
    parser.add_argument("--dry-run", action="store_true", help="List days to process without running evaluation.")
    args = parser.parse_args()

    period_dir = HISTORIC_DIR / args.period_name
    manifest_path = period_dir / "manifest.json"

    if not manifest_path.exists():
        print(f"ERROR: No manifest found at {manifest_path}. Run historic_fetch.py first.", file=sys.stderr)
        return 1

    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    department: Optional[int] = manifest.get("department")
    days_completed: List[str] = manifest.get("days_completed", [])

    if not days_completed:
        print("No completed days in manifest. Nothing to evaluate.", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps({
            "period_name": args.period_name,
            "department": department,
            "grace_minutes": args.grace_minutes,
            "days_to_evaluate": days_completed,
            "total_days": len(days_completed),
        }, indent=2))
        return 0

    from alfred.config import Config
    from alfred.data.loading.master_data_loader import MasterData, load_master_data
    from alfred.data.parsing.input_parser import InputParser
    from alfred.data.parsing.driver_directory_parser import DriverDirectoryParser
    from alfred.optimization.common.preassigned import reconstruct_preassigned_state
    from alfred.optimization.evaluation.solution_evaluator import evaluate_solution
    from alfred.optimization.settings.solver_settings import OptimizationSettings
    from alfred.pipeline.filters import filter_canceled_services
    from alfred.utils.logging_utils import setup_logging_context

    import pandas as pd

    setup_logging_context()
    Config.configure_logging()

    osrm_url = os.environ.get("OSRM_URL", "")
    if not osrm_url:
        print("ERROR: OSRM_URL environment variable is not set. OSRM is required for distance computation.", file=sys.stderr)
        return 1

    settings = OptimizationSettings()
    master_data = load_master_data(settings.master_data)

    # --- Load exclusion list (optional) ---
    exclusion_path = period_dir / "exclusion_list.json"
    excluded_labor_ids: set = set()
    excluded_service_ids: set = set()
    if exclusion_path.exists():
        with exclusion_path.open("r", encoding="utf-8") as f:
            excl = json.load(f)
        # Store both int and str forms so isin() matches regardless of column dtype
        raw_labor_ids = excl.get("excluded_labor_ids", [])
        raw_service_ids = excl.get("excluded_service_ids", [])
        excluded_labor_ids = {v for x in raw_labor_ids for v in (x, str(x))}
        excluded_service_ids = {v for x in raw_service_ids for v in (x, str(x))}
        logger.info(
            "exclusion_list_loaded path=%s labor_ids=%d service_ids=%d",
            exclusion_path.as_posix(), len(excluded_labor_ids), len(excluded_service_ids),
        )
        print(
            f"Exclusion list loaded: {len(excluded_labor_ids)} labor IDs, "
            f"{len(excluded_service_ids)} service IDs excluded.",
            flush=True,
        )

    kpi_rows: List[Dict[str, Any]] = []
    days_ok = 0
    days_skipped = 0
    days_failed = 0

    for day_str in days_completed:
        day = date.fromisoformat(day_str)
        day_dir = period_dir / day_str
        services_path = day_dir / "optimization_input_snapshot.json"
        drivers_path = day_dir / "driver_directory_snapshot.json"

        logger.info("evaluating_day date=%s", day_str)

        try:
            n_excluded = 0
            # --- Load and parse services ---
            with services_path.open("r", encoding="utf-8") as f:
                raw_input = json.load(f)

            labors_df, _ = InputParser.parse(raw_input)

            if labors_df is None or labors_df.empty:
                logger.warning("empty_labors_df date=%s — skipping", day_str)
                kpi_rows.append(_zero_kpi_row(day, args.period_name, department, "empty_input"))
                days_skipped += 1
                print(f"  [SKIP] {day_str}: no labors parsed", flush=True)
                continue

            labors_df = filter_canceled_services(labors_df)

            # --- Apply outlier exclusions ---
            n_before_exclusion = len(labors_df)
            if excluded_labor_ids and "labor_id" in labors_df.columns:
                labors_df = labors_df[~labors_df["labor_id"].isin(excluded_labor_ids)]
            if excluded_service_ids and "service_id" in labors_df.columns:
                labors_df = labors_df[~labors_df["service_id"].isin(excluded_service_ids)]

            # Drop labors with null-island coordinates (0.0, 0.0) — these produce
            # spurious multi-thousand-km driver moves and are always invalid data.
            for coord_col in ("start_address_point", "end_address_point", "map_start_point", "map_end_point"):
                if coord_col in labors_df.columns:
                    null_island_mask = labors_df[coord_col].astype(str).str.strip().isin(
                        {"POINT (0.0 0.0)", "POINT(0.0 0.0)", "POINT (0 0)", "POINT(0 0)"}
                    )
                    if null_island_mask.any():
                        dropped = int(null_island_mask.sum())
                        logger.warning(
                            "null_island_coords_dropped date=%s col=%s count=%d",
                            day_str, coord_col, dropped,
                        )
                        labors_df = labors_df[~null_island_mask]

            n_excluded = n_before_exclusion - len(labors_df)
            if n_excluded:
                logger.info("outliers_excluded date=%s count=%d", day_str, n_excluded)

            has_assigned = (
                "assigned_driver" in labors_df.columns
                and (
                    labors_df["assigned_driver"].notna()
                    & labors_df["assigned_driver"].astype(str).str.strip().ne("")
                ).any()
            )

            if not has_assigned:
                logger.warning("no_assigned_drivers date=%s — skipping", day_str)
                kpi_rows.append(_zero_kpi_row(day, args.period_name, department, "no_assigned_drivers", n_excluded))
                days_skipped += 1
                print(f"  [SKIP] {day_str}: no assigned_driver in data", flush=True)
                continue

            # --- Load and parse driver directory ---
            with drivers_path.open("r", encoding="utf-8") as f:
                raw_drivers = json.load(f)

            driver_directory_df = DriverDirectoryParser.parse(raw_drivers)

            # --- OSRM pre-computation ---
            dist_dict, time_dict = _precompute_osrm_distances(labors_df, driver_directory_df, master_data, osrm_url)
            enriched_master = MasterData(
                directorio_df=driver_directory_df,
                duraciones_df=master_data.duraciones_df,
                dist_dict=dist_dict,
            )

            # --- Reconstruct preassigned schedule ---
            preassigned_df, _, moves_df, recon_metrics = reconstruct_preassigned_state(
                labors_df,
                directorio_df=enriched_master.directorio_df,
                duraciones_df=enriched_master.duraciones_df,
                dist_method="osrm",
                dist_dict=enriched_master.dist_dict,
                model_params=settings.model_params,
                time_dict=time_dict if time_dict else None,
            )

            # --- Evaluate solution ---
            _, eval_report = evaluate_solution(
                preassigned_df,
                moves_df,
                driver_directory_df=driver_directory_df,
                grace_minutes=args.grace_minutes,
                default_shift_end=settings.model_params.workday_end_str,
            )

            # --- Persist full eval report ---
            eval_report_path = day_dir / "eval_report.json"
            with eval_report_path.open("w", encoding="utf-8") as f:
                json.dump(eval_report, f, indent=2, ensure_ascii=False, default=str)

            kpi_row = _extract_kpi_row(day, args.period_name, department, eval_report, recon_metrics, n_excluded)
            kpi_rows.append(kpi_row)
            days_ok += 1

            svc = kpi_row["services_total"]
            drivers = kpi_row["drivers_used"]
            util = kpi_row["utilization_with_moves_pct"]
            infeas = kpi_row["preassigned_infeasible"]
            excl_suffix = f", excluded={n_excluded}" if n_excluded else ""
            print(f"  [OK] {day_str}: {svc} services, {drivers} drivers, util={util:.1f}%, infeasible={infeas}{excl_suffix}", flush=True)

        except Exception as exc:
            logger.error("evaluate_day_failed date=%s error=%s", day_str, exc)
            traceback.print_exc()
            kpi_rows.append(_zero_kpi_row(day, args.period_name, department, f"error: {exc}", n_excluded))
            days_failed += 1
            print(f"  [FAIL] {day_str}: {exc}", flush=True)

    # --- Write CSV ---
    if kpi_rows:
        import pandas as pd
        kpis_df = pd.DataFrame(kpi_rows)
        kpis_df = kpis_df.sort_values("date").reset_index(drop=True)
        csv_path = period_dir / "daily_kpis.csv"
        kpis_df.to_csv(csv_path, index=False)
        logger.info("daily_kpis_written path=%s rows=%d", csv_path.as_posix(), len(kpis_df))
        print(f"\nWrote {len(kpis_df)} rows to {csv_path.as_posix()}", flush=True)

    print(
        f"\nDone. {days_ok} evaluated, {days_skipped} skipped, {days_failed} failed.",
        flush=True,
    )
    return 0 if days_failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
