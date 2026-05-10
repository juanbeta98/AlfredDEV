"""Scenario editor: manual labor reassignment and multi-scenario comparison.

Allows users to take an existing solution (output_payload.json), reassign
labors to different drivers, fully reconstruct the schedule, and compare KPIs
across multiple saved scenarios.

Public API
----------
ScenarioBase                Dataclass: loaded base solution ready for editing.
ScenarioSet                 Dataclass: N loaded scenarios ready for comparison.
load_scenario_base          Load a single solution for Section 1 editing.
load_driver_names           Build {driver_id: "First Last"} from driver directory JSON.
build_service_display_info  Build rich display metadata for each service/labor.
build_labor_editor_table    Build the editable assignment DataFrame.
apply_reassignments         Apply {labor_id → new_driver_id} to flat rows.
reconstruct_scenario        Recompute distances and timeline after reassignment.
save_scenario               Persist modified scenario to output_payload-compatible JSON.
load_scenarios_for_comparison  Load N scenarios into a ScenarioSet.
build_multi_scenario_overview_table  N-way KPI comparison table.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from alfred.analysis.solution_evaluation import (
    _get_labors,
    build_coord_lookups,
    build_points_lookup_from_rows,
    compute_payload_summary,
    filter_by_date,
    flatten_labors,
    infer_missing_durations,
    load_payload,
    reconstruct_timeline,
    recompute_move_distances,
)
from alfred.data.parsing.driver_directory_parser import DriverDirectoryParser
from alfred.optimization.settings.model_params import ModelParams
from alfred.optimization.settings.solver_settings import DEFAULT_DISTANCE_METHOD


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ScenarioBase:
    """All data derived from a single loaded solution, ready for editing."""

    services: List[Dict[str, Any]]
    rows: List[Dict[str, Any]]
    segments: List[Dict[str, Any]]
    drivers: List[str]
    all_services: List[str]
    points_lookup: Dict[Any, Tuple[Optional[str], Optional[str]]]
    driver_home_lookup: Dict[str, str]
    params: ModelParams


@dataclass
class ScenarioSet:
    """All data derived from N loaded scenarios, ready for comparison."""

    labels: List[str]
    rows_list: List[List[Dict[str, Any]]]
    segments_list: List[List[Dict[str, Any]]]
    summaries: List[Dict[str, Any]]
    all_drivers: List[str]
    all_services: List[str]
    points_lookup: Dict[Any, Tuple[Optional[str], Optional[str]]]
    driver_home_lookup: Optional[Dict[str, str]] = field(default=None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_driver_home_lookup(driver_directory_path: Optional[Path]) -> Dict[str, str]:
    """Load driver directory and return {driver_id_str: 'POINT(lon lat)'}."""
    if driver_directory_path is None or not driver_directory_path.exists():
        if driver_directory_path is not None:
            print(f"[warning] DRIVER_DIRECTORY not found: {driver_directory_path} — skipping home moves.")
        return {}
    dir_json = json.loads(driver_directory_path.read_text(encoding="utf-8"))
    dir_df = DriverDirectoryParser.parse(dir_json)
    if dir_df.empty:
        return {}
    lookup = {
        str(row["driver_id"]): f"POINT({row['longitud']} {row['latitud']})"
        for _, row in dir_df.iterrows()
    }
    print(f"[driver_home_lookup] {len(lookup)} drivers loaded")
    return lookup


# ---------------------------------------------------------------------------
# Public helpers for rich display metadata (used by the widget UI)
# ---------------------------------------------------------------------------


def load_driver_names(driver_directory_path: Optional[Path]) -> Dict[str, str]:
    """Return {driver_id_str: "First Last"} from a driver directory JSON.

    Falls back to the driver ID string when firstName/lastName are absent.
    Returns an empty dict when the file does not exist.
    """
    if driver_directory_path is None or not driver_directory_path.exists():
        return {}
    raw = json.loads(driver_directory_path.read_text(encoding="utf-8"))
    items: List[Dict[str, Any]] = (
        raw if isinstance(raw, list)
        else raw.get("results", raw.get("data", []))
    )
    names: Dict[str, str] = {}
    for item in items:
        driver_id = item.get("id")
        if driver_id is None:
            continue
        first = (item.get("firstName") or "").strip()
        last = (item.get("lastName") or "").strip()
        full = f"{first} {last}".strip() if (first or last) else str(driver_id)
        names[str(driver_id)] = full
    return names


def build_service_display_info(
    input_snapshot_path: Optional[Path],
) -> Dict[Any, Dict[str, Any]]:
    """Build rich display metadata for each labor from the optimization input snapshot.

    Returns a dict keyed by labor_id (int or str) with:
        labor_name     : human-readable labor name (e.g. "Alfred Initial Transport")
        service_id     : parent service ID
        from_address   : start address name string
        to_address     : end address name string
        schedule_date  : ISO schedule date string from the input
        labor_type     : labor_type string

    Returns an empty dict when the file does not exist or has no data.
    """
    if input_snapshot_path is None or not input_snapshot_path.exists():
        return {}
    raw = json.loads(input_snapshot_path.read_text(encoding="utf-8"))
    services: List[Dict[str, Any]] = (
        raw if isinstance(raw, list)
        else raw.get("data", raw.get("results", []))
    )
    info: Dict[Any, Dict[str, Any]] = {}
    for svc in services:
        service_id = svc.get("service_id")
        from_addr = (svc.get("start_address") or {}).get("name", "")
        to_addr = (svc.get("end_address") or {}).get("name", "")
        # Input snapshot uses snake_case key
        labors = svc.get("service_labors") or svc.get("serviceLabors") or []
        for lab in labors:
            lid = lab.get("id")
            if lid is None:
                continue
            info[lid] = {
                "labor_name":    lab.get("labor_name") or lab.get("labor_type") or "",
                "service_id":    service_id,
                "from_address":  from_addr,
                "to_address":    to_addr,
                "schedule_date": lab.get("schedule_date", ""),
                "labor_type":    lab.get("labor_type", ""),
            }
            # Also key by string for safe cross-type lookup
            info[str(lid)] = info[lid]
    return info


def _load_and_prepare_single(
    payload_path: Path,
    planning_date: Optional[str],
    coord_lookup: Optional[Dict],
    points_lookup_seed: Optional[Dict],
    vt_labor_ids: Optional[set],
    driver_home_lookup: Dict[str, str],
    params: ModelParams,
    distance_method: str,
) -> Tuple[List[Dict], List[Dict], List[Dict], Dict]:
    """Core loading pipeline for a single payload — shared by both public loaders."""
    services = load_payload(payload_path)
    if planning_date:
        services = filter_by_date(services, planning_date)

    # Build coordinate lookups from the payload itself when no external source provided
    local_coord_lookup = coord_lookup
    local_vt_ids = vt_labor_ids
    if local_coord_lookup is None:
        local_coord_lookup, _, local_vt_ids = build_coord_lookups(services, distance_method)

    rows, warnings = flatten_labors(
        services,
        local_coord_lookup,
        vt_labor_ids=local_vt_ids,
    )
    if warnings:
        print(f"[flatten_labors] {len(warnings)} distance divergence warning(s).")

    # Merge addData endpoints (solver coordinates) over address-derived ones
    pl = dict(points_lookup_seed) if points_lookup_seed else {}
    pl.update(build_points_lookup_from_rows(rows))

    # Recompute move distances using the merged point set and driver homes
    move_dists = recompute_move_distances(rows, pl, distance_method, driver_home_lookup)
    for r in rows:
        if r["labor_id"] in move_dists:
            r["driver_move_distance_km"] = move_dists[r["labor_id"]]

    infer_missing_durations(
        rows,
        params.vehicle_transport_speed_kmh,
        params.tiempo_alistar_min,
        params.tiempo_finalizacion_min,
    )

    segments = reconstruct_timeline(
        rows,
        params.alfred_speed_kmh,
        model_params=params,
        dist_method=distance_method,
    )

    return services, rows, segments, pl


# ---------------------------------------------------------------------------
# Public API — Section 1
# ---------------------------------------------------------------------------


def load_scenario_base(
    payload_path: Path,
    driver_directory_path: Optional[Path] = None,
    planning_date: Optional[str] = None,
    distance_method: str = DEFAULT_DISTANCE_METHOD,
    input_file: Optional[Path] = None,
) -> ScenarioBase:
    """Load a single solution and return a ScenarioBase ready for editing.

    Mirrors the loading half of compare_solutions.load_and_prepare for one
    solution, using the same coordinate and distance recomputation pipeline.

    Parameters
    ----------
    payload_path         : Path to output_payload.json (or a saved scenario JSON).
    driver_directory_path: Optional driver directory JSON for home-move distances.
    planning_date        : Drop labors outside this date (YYYY-MM-DD).
    distance_method      : Distance computation method (mirrors solver config).
    input_file           : Optional input snapshot with full address data for
                           recomputed labor distances (use when addData is absent).
    """
    params = ModelParams()
    driver_home_lookup = _build_driver_home_lookup(driver_directory_path)

    coord_lookup = points_lookup_seed = vt_labor_ids = None
    if input_file is not None:
        if not input_file.exists():
            print(f"[warning] INPUT_FILE not found: {input_file} — skipping computed distances.")
        else:
            raw = json.loads(input_file.read_text(encoding="utf-8"))
            input_svc = raw if isinstance(raw, list) else raw.get("data", [])
            coord_lookup, points_lookup_seed, vt_labor_ids = build_coord_lookups(
                input_svc, distance_method
            )
            print(f"[coord_lookup] {len(coord_lookup)} labors | method={distance_method!r}")

    services, rows, segments, points_lookup = _load_and_prepare_single(
        payload_path=payload_path,
        planning_date=planning_date,
        coord_lookup=coord_lookup,
        points_lookup_seed=points_lookup_seed,
        vt_labor_ids=vt_labor_ids,
        driver_home_lookup=driver_home_lookup,
        params=params,
        distance_method=distance_method,
    )

    drivers = sorted({r["driver_id"] for r in rows if r["driver_id"] is not None})
    all_services = sorted({str(r["service_id"]) for r in rows})

    print(
        f"[load_scenario_base] {len(rows)} labors | {len(drivers)} drivers | "
        f"{len(segments)} segments"
    )
    return ScenarioBase(
        services=services,
        rows=rows,
        segments=segments,
        drivers=drivers,
        all_services=all_services,
        points_lookup=points_lookup,
        driver_home_lookup=driver_home_lookup,
        params=params,
    )


def build_labor_editor_table(base: ScenarioBase) -> pd.DataFrame:
    """Build the editable assignment DataFrame for Section 1.

    Returns a DataFrame with one row per labor. The user edits the
    ``new_driver`` column to specify reassignments, then passes the result
    to apply_reassignments.

    Columns
    -------
    labor_id, service_id, labor_sequence, labor_type, actual_start (ISO str),
    original_driver, new_driver,
    labor_distance_km, driver_move_distance_km, is_infeasible,
    original_assigned_driver
    """
    records = []
    for r in base.rows:
        records.append({
            "labor_id":                r["labor_id"],
            "service_id":              r["service_id"],
            "labor_sequence":          r.get("service_labor_index", 0),
            "labor_type":              r["labor_type"],
            "actual_start":            r["actual_start"].isoformat() if r["actual_start"] else None,
            "original_driver":         r["driver_id"],
            "new_driver":              r["driver_id"],
            "labor_distance_km":       r["labor_distance_km"],
            "driver_move_distance_km": r["driver_move_distance_km"],
            "is_infeasible":           r["is_infeasible"],
            "original_assigned_driver": r.get("original_assigned_driver"),
        })

    df = pd.DataFrame(records)
    # Sort by actual_start then service_id / labor_sequence so co-service labors appear adjacent
    df = df.sort_values(["actual_start", "service_id", "labor_sequence"], ignore_index=True)
    return df


def apply_reassignments(
    rows: List[Dict[str, Any]],
    reassignments: Dict[Any, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply {labor_id → new_driver_id} reassignments to a copy of rows.

    Parameters
    ----------
    rows           : Flat labor rows from flatten_labors (not mutated).
    reassignments  : Mapping of labor_id → new driver_id string (or int).

    Returns
    -------
    new_rows  : Deep copy of rows with driver_id updated for reassigned labors
                and ``scenario_reassigned=True`` added to each changed row.
    warnings  : Human-readable strings for edge cases (preassignment overrides,
                partial service moves, unknown labor IDs).
    """
    new_rows: List[Dict[str, Any]] = copy.deepcopy(rows)
    warnings: List[str] = []

    rows_by_id: Dict[Any, Dict[str, Any]] = {r["labor_id"]: r for r in new_rows}

    for labor_id, new_driver in reassignments.items():
        row = rows_by_id.get(labor_id)
        if row is None:
            warnings.append(f"Labor {labor_id!r} not found in rows — skipped.")
            continue

        orig_preassigned = row.get("original_assigned_driver")
        if orig_preassigned is not None and str(new_driver) != str(orig_preassigned):
            warnings.append(
                f"Labor {labor_id}: has original_assigned_driver={orig_preassigned!r}; "
                f"reassigning to {new_driver!r} overrides the preassignment."
            )

        row["driver_id"] = str(new_driver) if new_driver is not None else None
        row["scenario_reassigned"] = True

    # Warn when only a subset of a multi-labor service is reassigned
    service_labors: Dict[Any, List[Any]] = {}
    for r in new_rows:
        service_labors.setdefault(r["service_id"], []).append(r["labor_id"])

    reassigned_ids: Set[Any] = set(reassignments.keys())
    for svc_id, labor_ids in service_labors.items():
        moved = [lid for lid in labor_ids if lid in reassigned_ids]
        if 0 < len(moved) < len(labor_ids):
            warnings.append(
                f"Service {svc_id}: {len(labor_ids)} labors total; only {len(moved)} reassigned "
                f"({moved}). The remaining {len(labor_ids) - len(moved)} stay with their original driver."
            )

    return new_rows, warnings


def reconstruct_scenario(
    rows: List[Dict[str, Any]],
    points_lookup: Dict[Any, Tuple[Optional[str], Optional[str]]],
    driver_home_lookup: Optional[Dict[str, str]],
    model_params: ModelParams,
    distance_method: str = DEFAULT_DISTANCE_METHOD,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Recompute move distances and timeline after reassignment.

    Recomputes driver_move_distance_km for every driver (re-chains assignments
    correctly since recompute_move_distances groups by driver_id and sorts
    chronologically), then rebuilds the timeline and propagates is_infeasible
    back into rows.

    Times (actual_start / actual_end) are NOT shifted — the solver's scheduled
    times are preserved and infeasibility is detected via timeline overlap.

    Returns
    -------
    rows     : Same list (mutated in-place) with updated move distances and
               is_infeasible flags.
    segments : Freshly reconstructed timeline segments.
    """
    move_dists = recompute_move_distances(rows, points_lookup, distance_method, driver_home_lookup)
    for r in rows:
        if r["labor_id"] in move_dists:
            r["driver_move_distance_km"] = move_dists[r["labor_id"]]

    segments = reconstruct_timeline(
        rows,
        model_params.alfred_speed_kmh,
        model_params=model_params,
        dist_method=distance_method,
    )

    # Propagate timeline-detected infeasibility back into rows
    infeasible_labor_ids: Set[Any] = {
        seg["labor_id"]
        for seg in segments
        if seg["segment_type"] == "VEHICLE_TRANSPORTATION" and seg.get("is_infeasible")
    }
    for r in rows:
        if r["labor_id"] in infeasible_labor_ids:
            r["is_infeasible"] = True

    return rows, segments


def save_scenario(
    original_services: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    output_path: Path,
    scenario_label: str,
) -> Path:
    """Persist a modified scenario to an output_payload-compatible JSON file.

    Patches the original services structure directly (rather than using
    OutputFormatter) to preserve start_address, end_address, and shop_address
    fields that build_route_map and build_coord_lookups require.

    The output file adds ``scenario_label`` and ``created_at`` keys at the root
    level; load_payload ignores them (it only reads ``raw["data"]``), so saved
    scenarios are immediately consumable by all existing analysis notebooks.

    Returns
    -------
    Path of the written file.
    """
    patched = copy.deepcopy(original_services)
    rows_by_id: Dict[Any, Dict[str, Any]] = {r["labor_id"]: r for r in rows}

    lab_key_candidates = ("service_labors", "serviceLabors")

    for svc in patched:
        lab_key = next((k for k in lab_key_candidates if k in svc), None)
        if lab_key is None:
            continue
        for labor in svc[lab_key]:
            labor_id = labor.get("id")
            r = rows_by_id.get(labor_id)
            if r is None:
                continue

            # Update driver assignment
            driver_id = r["driver_id"]
            labor["alfred"] = {"id": int(driver_id)} if driver_id is not None else None

            # Ensure addData exists
            if "addData" not in labor or labor["addData"] is None:
                labor["addData"] = {}

            labor["addData"]["driver_move_distance_km"] = r["driver_move_distance_km"]
            labor["addData"]["is_infeasible"] = r["is_infeasible"]
            labor["addData"]["duration_min"] = r["duration_min"]
            labor["addData"]["scenario_reassigned"] = bool(r.get("scenario_reassigned", False))

            # Preserve the original driver so downstream consumers can see what changed
            if r.get("scenario_reassigned"):
                orig_driver = r.get("original_assigned_driver")
                if orig_driver is None:
                    # Infer from the pre-patch alfred field (already updated above),
                    # so we need the value from the original row's pre-copy state.
                    # It was stored as a row field during load; fall back gracefully.
                    pass
                labor["addData"]["original_assigned_driver"] = orig_driver

    payload = {
        "scenario_label": scenario_label,
        "created_at": datetime.now().isoformat(),
        "data": patched,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

    print(f"[save_scenario] Saved {scenario_label!r} → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Public API — Section 2
# ---------------------------------------------------------------------------


def load_scenarios_for_comparison(
    scenario_paths: List[Tuple[Path, str]],
    driver_directory_path: Optional[Path] = None,
    planning_date: Optional[str] = None,
    distance_method: str = DEFAULT_DISTANCE_METHOD,
    input_file: Optional[Path] = None,
) -> ScenarioSet:
    """Load N scenario files into a ScenarioSet ready for comparison.

    Parameters
    ----------
    scenario_paths       : List of (path, label) tuples; first entry = baseline.
    driver_directory_path: Optional driver directory JSON.
    planning_date        : Drop labors outside this date (YYYY-MM-DD).
    distance_method      : Distance computation method.
    input_file           : Optional shared input snapshot for distance recomputation.
    """
    params = ModelParams()
    driver_home_lookup = _build_driver_home_lookup(driver_directory_path)

    # Build coordinate lookups once from the shared input mirror (if provided)
    coord_lookup = points_lookup_seed = vt_labor_ids = None
    if input_file is not None:
        if not input_file.exists():
            print(f"[warning] INPUT_FILE not found: {input_file} — skipping computed distances.")
        else:
            raw = json.loads(input_file.read_text(encoding="utf-8"))
            input_svc = raw if isinstance(raw, list) else raw.get("data", [])
            coord_lookup, points_lookup_seed, vt_labor_ids = build_coord_lookups(
                input_svc, distance_method
            )
            print(f"[coord_lookup] {len(coord_lookup)} labors | method={distance_method!r}")

    all_labels: List[str] = []
    all_rows_list: List[List[Dict[str, Any]]] = []
    all_segments_list: List[List[Dict[str, Any]]] = []
    all_summaries: List[Dict[str, Any]] = []
    combined_points_lookup: Dict[Any, Tuple[Optional[str], Optional[str]]] = (
        dict(points_lookup_seed) if points_lookup_seed else {}
    )

    for path, label in scenario_paths:
        print(f"\n[load_scenarios] Loading {label!r} from {path.name}")
        _, rows, segments, pl = _load_and_prepare_single(
            payload_path=path,
            planning_date=planning_date,
            coord_lookup=coord_lookup,
            points_lookup_seed=points_lookup_seed,
            vt_labor_ids=vt_labor_ids,
            driver_home_lookup=driver_home_lookup,
            params=params,
            distance_method=distance_method,
        )
        combined_points_lookup.update(pl)

        summary = compute_payload_summary(
            rows,
            tiempo_gracia_min=params.tiempo_gracia_min,
            segments=segments,
        )

        all_labels.append(label)
        all_rows_list.append(rows)
        all_segments_list.append(segments)
        all_summaries.append(summary)

        n_drivers = len({r["driver_id"] for r in rows if r["driver_id"] is not None})
        print(f"  {len(rows)} labors | {n_drivers} drivers | {len(segments)} segments")

    all_drivers = sorted(
        {r["driver_id"] for rows in all_rows_list for r in rows if r["driver_id"] is not None}
    )
    all_services = sorted(
        {str(r["service_id"]) for rows in all_rows_list for r in rows}
    )

    return ScenarioSet(
        labels=all_labels,
        rows_list=all_rows_list,
        segments_list=all_segments_list,
        summaries=all_summaries,
        all_drivers=all_drivers,
        all_services=all_services,
        points_lookup=combined_points_lookup,
        driver_home_lookup=driver_home_lookup if driver_home_lookup else None,
    )


def build_multi_scenario_overview_table(
    scenario_set: ScenarioSet,
    baseline_label: Optional[str] = None,
) -> pd.DataFrame:
    """Build an N-way KPI comparison table with Δ columns vs a baseline scenario.

    Parameters
    ----------
    scenario_set   : ScenarioSet from load_scenarios_for_comparison.
    baseline_label : Label of the baseline scenario (default: first scenario).

    Returns
    -------
    DataFrame indexed by metric with one column per scenario and one
    Δ {label} column per non-baseline scenario.
    """
    _METRICS: List[Tuple[str, str]] = [
        ("services_count",                "services"),
        ("labors_count",                  "labors_total"),
        ("labors_vt_count",               "labors_vt"),
        ("labors_non_vt_count",           "labors_non_vt"),
        ("drivers_count",                 "drivers_used"),
        ("labors_assigned",               "labors_assigned"),
        ("labors_infeasible",             "labors_infeasible"),
        ("labors_infeasible_pct",         "labors_infeasible_pct"),
        ("labors_in_grace",               "labors_in_grace"),
        ("total_grace_min",               "total_grace_min"),
        ("labors_reassignment_candidate", "reassignment_candidates"),
        ("total_labor_distance_km",       "total_labor_distance_km"),
        ("total_driver_move_distance_km", "total_driver_move_distance_km"),
        ("total_distance_km",             "total_distance_km"),
        ("avg_labor_distance_km",         "avg_labor_distance_km"),
        ("avg_driver_move_distance_km",   "avg_driver_move_distance_km"),
    ]

    # Enrich summaries with non_vt count
    summaries = []
    for s in scenario_set.summaries:
        enriched = dict(s)
        enriched["labors_non_vt_count"] = enriched["labors_count"] - enriched["labors_vt_count"]
        summaries.append(enriched)

    baseline_idx = 0
    if baseline_label is not None and baseline_label in scenario_set.labels:
        baseline_idx = scenario_set.labels.index(baseline_label)
    baseline_summary = summaries[baseline_idx]
    baseline_lbl = scenario_set.labels[baseline_idx]

    table_rows: List[Dict[str, Any]] = []
    for key, metric in _METRICS:
        row: Dict[str, Any] = {"metric": metric}
        for i, (label, summary) in enumerate(zip(scenario_set.labels, summaries)):
            row[label] = summary.get(key)
        for i, label in enumerate(scenario_set.labels):
            if i == baseline_idx:
                continue
            va = baseline_summary.get(key)
            vb = summaries[i].get(key)
            try:
                delta = round(float(vb) - float(va), 4) if va is not None and vb is not None else None
            except (TypeError, ValueError):
                delta = None
            row[f"Δ {label} vs {baseline_lbl}"] = delta
        table_rows.append(row)

    return pd.DataFrame(table_rows).set_index("metric")
