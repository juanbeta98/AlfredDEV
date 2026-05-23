"""
Inspect per-labor distance details for a single historic day.

Reconstructs the preassigned schedule for a given day and writes
labor_details.csv to the day directory, then prints a table sorted by
dist_km descending. Use this to identify anomalously large-distance labors
(e.g. inter-city services) before populating an exclusion_list.json.

Usage (from repo root):
    scripts/mrun python scripts/analysis/historic_inspect_labors.py \\
        --period-name before --date 2025-02-03

    # Show only top-N by distance
    scripts/mrun python scripts/analysis/historic_inspect_labors.py \\
        --period-name before --date 2025-02-03 --top 20
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

def _find_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "data").exists() and (candidate / "src").exists():
            return candidate
    raise RuntimeError("Could not resolve repository root from script path.")


ROOT = _find_repo_root()
sys.path.insert(0, str(ROOT / "src"))

logger = logging.getLogger(__name__)

HISTORIC_DIR = ROOT / "data" / "historic_analysis"

# Columns to pull from preassigned_df for the per-labor CSV
_DETAIL_COLS = [
    "labor_id",
    "service_id",
    "department_code",
    "city_code",
    "assigned_driver",
    "labor_type",
    "labor_category",
    "start_address_point",
    "end_address_point",
    "dist_km",
    "actual_start",
    "actual_end",
    "is_infeasible",
    "infeasibility_cause_code",
]


def _precompute_osrm_distances(labors_df, driver_directory_df, master_data, osrm_url: str):
    """Same pre-computation as in historic_evaluate.py."""
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

        batch_dist, batch_time = batch_distance_matrix(all_pts, all_pts, osrm_url, include_times=True)
        if batch_dist:
            existing = dist_dict.get(dept_str, {})
            dist_dict[dept_str] = {**batch_dist, **existing}
            time_dict.update(batch_time)

    return dist_dict, time_dict


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect per-labor distances for a single historic day."
    )
    parser.add_argument("--period-name", required=True, help="Period name matching a folder in data/historic_analysis/.")
    parser.add_argument("--date", required=True, help="Date to inspect, YYYY-MM-DD.")
    parser.add_argument("--top", type=int, default=None, help="Print only the top-N labors by dist_km (default: all).")
    args = parser.parse_args()

    day = date.fromisoformat(args.date)
    day_str = day.isoformat()
    period_dir = HISTORIC_DIR / args.period_name
    day_dir = period_dir / day_str

    services_path = day_dir / "optimization_input_snapshot.json"
    drivers_path = day_dir / "driver_directory_snapshot.json"

    for p in (services_path, drivers_path):
        if not p.exists():
            print(f"ERROR: Missing snapshot: {p}", file=sys.stderr)
            print("Run historic_fetch.py first.", file=sys.stderr)
            return 1

    from alfred.config import Config
    from alfred.data.loading.master_data_loader import MasterData, load_master_data
    from alfred.data.parsing.input_parser import InputParser
    from alfred.data.parsing.driver_directory_parser import DriverDirectoryParser
    from alfred.optimization.common.preassigned import reconstruct_preassigned_state
    from alfred.optimization.settings.solver_settings import OptimizationSettings
    from alfred.pipeline.filters import filter_canceled_services
    from alfred.utils.logging_utils import setup_logging_context

    import pandas as pd

    setup_logging_context()
    Config.configure_logging()

    osrm_url = os.environ.get("OSRM_URL", "")
    if not osrm_url:
        print("ERROR: OSRM_URL environment variable is not set.", file=sys.stderr)
        return 1

    settings = OptimizationSettings()
    master_data = load_master_data(settings.master_data)

    # --- Load and parse services ---
    with services_path.open("r", encoding="utf-8") as f:
        raw_input = json.load(f)

    labors_df, _ = InputParser.parse(raw_input)

    if labors_df is None or labors_df.empty:
        print(f"No labors parsed for {day_str}.", file=sys.stderr)
        return 1

    labors_df = filter_canceled_services(labors_df)

    # Flag null-island coordinates so they're visible in the output
    _null_island_variants = {"POINT (0.0 0.0)", "POINT(0.0 0.0)", "POINT (0 0)", "POINT(0 0)"}
    for _coord_col in ("start_address_point", "end_address_point"):
        if _coord_col in labors_df.columns:
            _bad = labors_df[_coord_col].astype(str).str.strip().isin(_null_island_variants)
            if _bad.any():
                print(
                    f"\n[WARN] {int(_bad.sum())} labors have null-island coordinates "
                    f"in {_coord_col} — these will cause spurious move distances.",
                    flush=True,
                )
                print(f"  labor_ids: {labors_df.loc[_bad, 'labor_id'].tolist()}", flush=True)

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
    preassigned_df, _, _, _ = reconstruct_preassigned_state(
        labors_df,
        directorio_df=enriched_master.directorio_df,
        duraciones_df=enriched_master.duraciones_df,
        dist_method="osrm",
        dist_dict=enriched_master.dist_dict,
        model_params=settings.model_params,
        time_dict=time_dict if time_dict else None,
    )

    if preassigned_df is None or preassigned_df.empty:
        print(f"Reconstruction produced no rows for {day_str}.", file=sys.stderr)
        return 1

    # --- Build details DataFrame ---
    available_cols = [c for c in _DETAIL_COLS if c in preassigned_df.columns]
    details_df = preassigned_df[available_cols].copy()

    # Merge in input columns not present in preassigned_df
    for extra_col in ["labor_type", "labor_category", "city_code", "start_address_point", "end_address_point"]:
        if extra_col not in details_df.columns and extra_col in labors_df.columns:
            details_df = details_df.merge(
                labors_df[["labor_id", extra_col]].drop_duplicates("labor_id"),
                on="labor_id",
                how="left",
            )

    details_df = details_df.sort_values("dist_km", ascending=False).reset_index(drop=True)

    # --- Write CSV ---
    csv_path = day_dir / "labor_details.csv"
    details_df.to_csv(csv_path, index=False)
    print(f"Wrote {len(details_df)} rows to {csv_path.as_posix()}", flush=True)

    # --- Print table ---
    display_df = details_df if args.top is None else details_df.head(args.top)
    print_cols = [c for c in ["labor_id", "service_id", "department_code", "city_code", "assigned_driver", "dist_km", "is_infeasible"] if c in display_df.columns]

    print(f"\n{'─'*80}")
    header = args.period_name + " / " + day_str
    if args.top:
        header += f"  (top {args.top} by dist_km)"
    print(header)
    print(f"{'─'*80}")
    print(display_df[print_cols].to_string(index=True))
    print(f"\nFull details written to: {csv_path.as_posix()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
