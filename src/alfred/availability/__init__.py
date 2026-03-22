"""
src/availability — Time slot availability module.

Public API
----------
check_availability(request) -> AvailabilityResponse
    Top-level entry point.  Loads the live schedule from the ALFRED API
    and checks whether the customer's desired time slot is feasible.
    If not, scans 30-minute intervals from as_of_time to workday_end and
    returns all feasible alternatives.

Typical usage
-------------
    from alfred.availability import check_availability
    from alfred.availability.models import LaborRequest, ServiceRequest
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Bogota")
    request = ServiceRequest(
        service_id="SVC-001",
        department_code="25",
        labors=[
            LaborRequest(
                labor_sequence=1,
                labor_category="VEHICLE_TRANSPORTATION",
                labor_type="TRASLADO_VEHICULO",
                map_start_point="POINT (-74.0817 4.6097)",
                map_end_point="POINT (-74.1234 4.6543)",
            )
        ],
        desired_slot=datetime(2026, 3, 4, 9, 0, tzinfo=tz),
        as_of_time=datetime(2026, 3, 4, 7, 30, tzinfo=tz),
    )
    response = check_availability(request)
"""

import logging

from alfred.availability.models import AvailabilityResponse, ServiceRequest, TimeSlotResult
from alfred.availability.pico_placa import extract_plate_digit, is_plate_restricted
from alfred.availability.request_parser import parse_api_request
from alfred.availability.schedule_loader import load_schedule_state
from alfred.availability.slot_scanner import scan_availability
from alfred.optimization.settings.solver_settings import OptimizationSettings

logger = logging.getLogger(__name__)


def check_availability(request: ServiceRequest) -> AvailabilityResponse:
    """
    Check time slot availability for a new service request.

    If a license_plate is provided on the request, first checks whether the
    plate is subject to a pico y placa restriction on the desired date.
    If restricted, returns immediately with reason "pico_y_placa" without
    hitting the schedule API.

    Otherwise, loads the current live schedule from the ALFRED API, then
    probes the customer's desired time slot.  If infeasible, scans 30-minute
    intervals from request.as_of_time to workday_end and returns all feasible
    slots.

    Args:
        request: ServiceRequest with labor definitions, desired_slot, and
                 as_of_time.

    Returns:
        AvailabilityResponse with desired_slot_result and feasible_slots.

    Raises:
        ScheduleLoadError: If the live schedule cannot be loaded.
    """
    # --- Pico y placa gate (runs before any schedule I/O) ---
    plate = request.license_plate
    if plate is not None:
        digit = extract_plate_digit(plate)
        if digit is not None:
            check_date = request.desired_slot.date()
            if is_plate_restricted(digit, request.department_code, check_date):
                logger.info(
                    "pico_y_placa_restricted service_id=%s dept=%s plate=%s date=%s",
                    request.service_id,
                    request.department_code,
                    plate,
                    check_date,
                )
                return AvailabilityResponse(
                    service_id=request.service_id,
                    desired_slot_result=TimeSlotResult(
                        slot_time=request.desired_slot,
                        feasible=False,
                        reason="pico_y_placa",
                    ),
                    feasible_slots=[],
                    scan_performed=False,
                    total_slots_checked=1,
                    schedule_date_str=str(check_date),
                )
        else:
            logger.warning(
                "pico_y_placa_invalid_plate service_id=%s plate=%r — skipping restriction check",
                request.service_id,
                plate,
            )

    settings = OptimizationSettings(algorithm="INSERT")
    state = load_schedule_state(
        department_code=request.department_code,
        schedule_date=request.desired_slot.date(),
        settings=settings,
    )
    return scan_availability(request, state)


__all__ = [
    "check_availability",
    "parse_api_request",
    "ServiceRequest",
    "AvailabilityResponse",
]
