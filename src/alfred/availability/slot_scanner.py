"""
slot_scanner.py — Time slot interval sweep.

Implements the two-phase availability check:
  1. Probe the customer's desired_slot first.
  2. If infeasible, generate all 30-minute slots from as_of_time to workday_end
     and probe each one, returning the list of feasible slots.
"""

import logging
import math
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from alfred.availability.feasibility_probe import _city_dist_slice, probe_slot
from alfred.availability.models import (
    AvailabilityResponse,
    ScheduleState,
    ServiceRequest,
    TimeSlotResult,
)
from alfred.optimization.common.distance_utils import batch_distance_matrix
from alfred.optimization.common.utils import compute_workday_end

logger = logging.getLogger(__name__)

COLOMBIA_TZ = "America/Bogota"


def _round_up_to_interval(dt: datetime, interval_minutes: int) -> datetime:
    """Round a datetime UP to the next N-minute boundary."""
    total_minutes = dt.hour * 60 + dt.minute
    rounded = math.ceil(total_minutes / interval_minutes) * interval_minutes
    base = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=rounded)


def _generate_slots(
    as_of_time: datetime,
    workday_end_dt,
    interval_minutes: int,
) -> List[datetime]:
    """Generate interval-spaced slots from as_of_time (rounded up) to workday_end."""
    start = _round_up_to_interval(as_of_time, interval_minutes)

    # Ensure timezone consistency with workday_end_dt
    if (
        workday_end_dt is not None
        and hasattr(workday_end_dt, "tzinfo")
        and workday_end_dt.tzinfo is not None
        and start.tzinfo is None
    ):
        start = start.replace(tzinfo=ZoneInfo(COLOMBIA_TZ))

    slots: List[datetime] = []
    current = start
    while current <= workday_end_dt:
        slots.append(current)
        current = current + timedelta(minutes=interval_minutes)
    return slots


def scan_availability(
    request: ServiceRequest,
    state: ScheduleState,
    slot_interval_minutes: int = 30,
) -> AvailabilityResponse:
    """
    Check the desired slot first; if infeasible, scan all 30-min slots from
    as_of_time to workday_end and return every feasible one.

    Args:
        request              : ServiceRequest with desired_slot and as_of_time.
        state                : Frozen preassigned schedule state for the day.
        slot_interval_minutes: Interval between candidate slots (default 30).

    Returns:
        AvailabilityResponse with desired_slot_result and feasible_slots.
    """
    model_params = state.settings.model_params
    workday_end_dt = compute_workday_end(
        day_str=state.day_str,
        workday_end_str=model_params.workday_end_str,
        tzinfo=COLOMBIA_TZ,
    )

    # --- OSRM batch pre-computation (mirrors OFFLINE algorithm pattern) ---
    # Build the full distance matrix once so all slot probes start with a warm
    # cache and make zero individual OSRM HTTP calls.
    _precomputed_dist_dict: Optional[Dict] = None
    if state.settings.distance_method == "osrm":
        _osrm_url = os.environ.get("OSRM_URL", "")
        if _osrm_url:
            city_key = state.department_code
            _driver_positions = [
                f"POINT ({row.longitud} {row.latitud})"
                for _, row in state.master_data.directorio_df.iterrows()
            ]
            _base_starts = (
                state.base_labors_df["map_start_point"].dropna().unique().tolist()
                if not state.base_labors_df.empty and "map_start_point" in state.base_labors_df.columns
                else []
            )
            _base_ends = (
                state.base_labors_df["map_end_point"].dropna().unique().tolist()
                if not state.base_labors_df.empty and "map_end_point" in state.base_labors_df.columns
                else []
            )
            _req_starts = [lb.map_start_point for lb in request.labors if lb.map_start_point]
            _req_ends   = [lb.map_end_point   for lb in request.labors if lb.map_end_point]
            _all_points = list(dict.fromkeys(
                _driver_positions + _base_starts + _base_ends + _req_starts + _req_ends
            ))
            _precomp_dist, _ = batch_distance_matrix(
                _all_points, _all_points, _osrm_url,
                include_times=(state.settings.time_method == "osrm_times"),
            )
            if _precomp_dist:
                _city_cache = _city_dist_slice(state.master_data.dist_dict, city_key)
                _precomputed_dist_dict = {**_precomp_dist, **_city_cache}
                logger.info(
                    "availability_osrm_precompute city=%s unique_points=%d pairs=%d",
                    city_key, len(_all_points), len(_precomp_dist),
                )
            else:
                logger.warning(
                    "availability_osrm_precompute_failed city=%s — probes will use per-call fallback",
                    city_key,
                )

    # Phase 1: check desired slot
    desired_result = probe_slot(request.desired_slot, request, state, dist_dict=_precomputed_dist_dict)

    if desired_result.feasible:
        logger.info(
            "availability_desired_slot_feasible service_id=%s slot=%s",
            request.service_id,
            pd.Timestamp(request.desired_slot).isoformat(),
        )
        return AvailabilityResponse(
            service_id=request.service_id,
            desired_slot_result=desired_result,
            feasible_slots=[],
            scan_performed=False,
            total_slots_checked=1,
            schedule_date_str=state.day_str,
        )

    # Phase 2: full interval scan
    slots = _generate_slots(request.as_of_time, workday_end_dt, slot_interval_minutes)

    logger.info(
        "availability_scanning service_id=%s slots=%d interval_min=%d",
        request.service_id, len(slots), slot_interval_minutes,
    )

    feasible_slots: List[TimeSlotResult] = []
    for slot in slots:
        result = probe_slot(slot, request, state, dist_dict=_precomputed_dist_dict)
        if result.feasible:
            feasible_slots.append(result)

    logger.info(
        "availability_scan_done service_id=%s feasible=%d/%d",
        request.service_id, len(feasible_slots), len(slots),
    )

    return AvailabilityResponse(
        service_id=request.service_id,
        desired_slot_result=desired_result,
        feasible_slots=feasible_slots,
        scan_performed=True,
        total_slots_checked=len(slots) + 1,
        schedule_date_str=state.day_str,
    )
