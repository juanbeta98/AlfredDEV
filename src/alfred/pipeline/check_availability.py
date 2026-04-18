#!/usr/bin/env python3
"""
check_availability.py — ALFRED time slot availability checker.

Validates whether a new service can be assigned at a customer's desired time
and, if not, returns the list of feasible 30-minute slots for the day.

Supports two request formats (auto-detected):

  API format (address-centric, from customer-facing endpoint):
  ------------------------------------------------------------
  {
      "department_id": 25,
      "department_name": "CUNDINAMARCA",
      "date": "2026-03-09T14:30:00-05:00",
      "start_address": {
          "id": 123456,
          "name": "Calle 123",
          "city": "Bogotá",
          "department": "CUNDINAMARCA",
          "point": {"x": -74.08, "y": 4.71, "srid": 4326}
      },
      "end_address": {
          "id": 123456,
          "name": "Calle 123",
          "city": "Bogotá",
          "department": "CUNDINAMARCA",
          "point": {"x": -74.05, "y": 4.62, "srid": 4326}
      }
  }

  Internal format (explicit labors, for programmatic use):
  ---------------------------------------------------------
  {
      "service_id": "SVC-001",
      "department_code": "25",
      "desired_slot": "2026-03-04T09:00:00-05:00",
      "as_of_time":   "2026-03-04T07:30:00-05:00",
      "labors": [
          {
              "labor_sequence": 1,
              "labor_category": "VEHICLE_TRANSPORTATION",
              "labor_type": "TRASLADO_VEHICULO",
              "map_start_point": "POINT (-74.0817 4.6097)",
              "map_end_point":   "POINT (-74.1234 4.6543)"
          }
      ]
  }

Output JSON format
------------------
ok — desired slot is feasible:
{
    "data": {
        "result": "ok",
        "message": "Horario disponible",
        "department": "CUNDINAMARCA",
        "requested_schedule": "2026-03-04T09:00:00",
        "confirmed_schedule": "2026-03-04T09:00:00"
    }
}

occupied — desired slot infeasible, no alternatives:
{
    "data": {
        "result": "occupied",
        "message": "No hay disponibilidad para este día, seleccione otra fecha",
        "department": "CUNDINAMARCA",
        "requested_schedule": "2026-03-04T09:00:00"
    }
}

reschedule — desired slot infeasible, alternatives found:
{
    "data": {
        "result": "reschedule",
        "message": "Horario no disponible, se sugieren alternativas",
        "department": "CUNDINAMARCA",
        "requested_schedule": "2026-03-04T09:00:00",
        "available_schedules": [
            "2026-03-04T10:00:00",
            "2026-03-04T10:30:00"
        ]
    }
}

error — unrecoverable failure:
{
    "data": {
        "result": "error",
        "message": "<error description>",
        "department": null,
        "requested_schedule": null
    }
}
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from alfred.config import Config
from alfred.availability.exceptions import LicenseError
from alfred.availability.models import AvailabilityResponse, LaborRequest, ServiceRequest, TimeSlotResult
from alfred.availability.pico_placa import extract_plate_digit, is_plate_restricted
from alfred.availability.request_parser import parse_api_request
from alfred.availability.schedule_loader import load_schedule_state
from alfred.availability.slot_scanner import scan_availability
from alfred.data.io.artifact_naming import build_run_subdir, finalize_run_manifest, write_run_manifest
from alfred.optimization.settings.solver_settings import OptimizationSettings

logger = logging.getLogger(__name__)


def _validate_license() -> None:
    pass  # License enforcement is injected by build_prod.sh in production builds.


def run(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the availability check for a pre-loaded request dict.

    Returns the serialized response dict (same shape as the JSON output).
    Each stage is timed individually; results are written to a run directory
    under Config.RUNS_DIR (unless Config.DISABLE_FILE_OUTPUT is set).
    """
    Config.validate()
    Config.configure_logging()
    _validate_license()

    department = data.get("department_name") or data.get("department_code") or None
    run_id = str(data.get("service_id") or data.get("department_id") or "local")

    run_dir: Optional[Path] = None
    started_at = datetime.now(timezone.utc)
    if not Config.DISABLE_FILE_OUTPUT:
        run_dir = Path(Config.RUNS_DIR) / build_run_subdir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        write_run_manifest(run_dir, run_id, created_at=started_at)

    stage_timings: Dict[str, Any] = {}
    t_total = time.perf_counter()

    try:
        request = _parse_request(data)
    except (KeyError, ValueError, TypeError) as exc:
        logger.error("Invalid request format: %s", exc)
        result = _error_dict(str(exc))
        _finalize_and_write(run_dir, result, "error", time.perf_counter() - t_total, stage_timings)
        return result

    # --- Stage 1: pico_y_placa_check ---
    _t = time.perf_counter()
    plate = request.license_plate
    pico_blocked = False
    if plate is not None:
        digit = extract_plate_digit(plate)
        if digit is not None:
            pico_blocked = is_plate_restricted(digit, request.department_code, request.desired_slot.date())
        else:
            logger.warning(
                "pico_y_placa_invalid_plate service_id=%s plate=%r — skipping restriction check",
                request.service_id, plate,
            )
    stage_timings["pico_y_placa_check"] = round(time.perf_counter() - _t, 3)

    if pico_blocked:
        logger.info(
            "pico_y_placa_restricted service_id=%s dept=%s plate=%s date=%s",
            request.service_id, request.department_code, plate, request.desired_slot.date(),
        )
        response = AvailabilityResponse(
            service_id=request.service_id,
            desired_slot_result=TimeSlotResult(
                slot_time=request.desired_slot,
                feasible=False,
                reason="pico_y_placa",
            ),
            feasible_slots=[],
            scan_performed=False,
            total_slots_checked=1,
            schedule_date_str=str(request.desired_slot.date()),
        )
        elapsed = time.perf_counter() - t_total
        result = _serialize_response(response, department, round(elapsed, 3))
        _finalize_and_write(run_dir, result, "success", elapsed, stage_timings)
        return result

    # --- Stage 2: schedule_load (sub-staged inside load_schedule_state) ---
    try:
        settings = OptimizationSettings(algorithm="INSERT")
        state, schedule_sub_timings = load_schedule_state(
            department_code=request.department_code,
            schedule_date=request.desired_slot.date(),
            settings=settings,
        )
    except Exception as exc:
        logger.exception("availability_schedule_load_failed")
        stage_timings["schedule_load"] = {}
        result = _error_dict(str(exc))
        _finalize_and_write(run_dir, result, "error", time.perf_counter() - t_total, stage_timings)
        return result
    stage_timings["schedule_load"] = schedule_sub_timings

    # --- Stage 3: desired_slot_probe / slot_scan ---
    _t = time.perf_counter()
    try:
        response = scan_availability(request, state)
    except Exception as exc:
        logger.exception("availability_check_failed")
        stage_timings["slot_scan"] = round(time.perf_counter() - _t, 3)
        result = _error_dict(str(exc))
        _finalize_and_write(run_dir, result, "error", time.perf_counter() - t_total, stage_timings)
        return result

    scan_elapsed = round(time.perf_counter() - _t, 3)
    stage_timings["slot_scan" if response.scan_performed else "desired_slot_probe"] = scan_elapsed

    elapsed = time.perf_counter() - t_total
    result = _serialize_response(response, department, round(elapsed, 3))
    _finalize_and_write(run_dir, result, "success", elapsed, stage_timings)
    return result


def _finalize_and_write(
    run_dir: Optional[Path],
    result: Dict[str, Any],
    status: str,
    elapsed_seconds: float,
    stage_timings: Dict[str, Any],
) -> None:
    """Write output_payload.json and finalize run.json. Silently skips if file output is disabled."""
    if Config.DISABLE_FILE_OUTPUT or run_dir is None:
        return
    try:
        out_dir = run_dir / "output"
        out_dir.mkdir(exist_ok=True)
        with (out_dir / "output_payload.json").open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        logger.info("local_output_saved artifact=output_payload_json")
        result_type = (result.get("data") or {}).get("result")
        finalize_run_manifest(
            run_dir,
            status=status,
            duration_seconds=elapsed_seconds,
            extra_fields={"result": result_type, "stage_timings_seconds": stage_timings},
        )
    except Exception:
        logger.exception("check_availability_run_dir_write_failed")


def main() -> int:
    Config.validate()
    Config.configure_logging()

    parser = argparse.ArgumentParser(
        description="ALFRED time slot availability checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--request",
        required=True,
        metavar="PATH",
        help="Path to the availability request JSON file",
    )
    args = parser.parse_args()

    try:
        with open(args.request, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to load request file %s: %s", args.request, exc)
        return 1

    result = run(data)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("data", {}).get("result") != "error" else 1


def _parse_request(data: Dict[str, Any]) -> ServiceRequest:
    """
    Auto-detect and parse either the API format (department_id + addresses)
    or the internal format (department_code + labors array).
    """
    if "department_id" in data:
        # API / customer-facing format
        return parse_api_request(data)

    # Internal / programmatic format
    labors = [
        LaborRequest(
            labor_sequence=int(lb["labor_sequence"]),
            labor_category=lb["labor_category"],
            map_start_point=lb["map_start_point"],
            map_end_point=lb["map_end_point"],
            labor_type=lb.get("labor_type"),
            estimated_time=float(lb["estimated_time"]) if lb.get("estimated_time") is not None else None,
            labor_name=lb.get("labor_name"),
        )
        for lb in data["labors"]
    ]
    return ServiceRequest(
        service_id=str(data["service_id"]),
        department_code=str(data["department_code"]),
        labors=labors,
        desired_slot=datetime.fromisoformat(data["desired_slot"]),
        as_of_time=datetime.fromisoformat(data["as_of_time"]),
        license_plate=data.get("license_plate") or None,
    )


def _serialize_response(
    response: AvailabilityResponse,
    department: Optional[str],
    elapsed_seconds: float = 0.0,
) -> Dict[str, Any]:
    requested = _fmt_ts(response.desired_slot_result.slot_time)

    if response.error:
        return _error_dict(response.error)

    if response.desired_slot_result.feasible:
        return {"data": {
            "result": "ok",
            "message": "Horario disponible",
            "department": department,
            "requested_schedule": requested,
            "confirmed_schedule": requested,
            "elapsed_seconds": elapsed_seconds,
        }}

    if not response.feasible_slots:
        reason = response.desired_slot_result.reason
        if reason == "pico_y_placa":
            return {"data": {
                "result": "pico_y_placa",
                "message": "Vehículo restringido por pico y placa para esta fecha",
                "department": department,
                "requested_schedule": requested,
                "elapsed_seconds": elapsed_seconds,
            }}
        return {"data": {
            "result": "occupied",
            "message": "No hay disponibilidad para este día, seleccione otra fecha",
            "department": department,
            "requested_schedule": requested,
            "elapsed_seconds": elapsed_seconds,
        }}

    return {"data": {
        "result": "reschedule",
        "message": "Horario no disponible, se sugieren alternativas",
        "department": department,
        "requested_schedule": requested,
        "available_schedules": [_fmt_ts(s.slot_time) for s in response.feasible_slots],
        "elapsed_seconds": elapsed_seconds,
    }}


def _error_dict(message: str) -> Dict[str, Any]:
    return {"data": {
        "result": "error",
        "message": message,
        "department": None,
        "requested_schedule": None,
    }}


def _fmt_ts(ts: Optional[Any]) -> Optional[str]:
    if ts is None:
        return None
    if hasattr(ts, "replace"):
        # Strip timezone info to produce naive ISO format (YYYY-MM-DDTHH:MM:SS)
        return ts.replace(tzinfo=None).isoformat()
    return str(ts)


if __name__ == "__main__":
    sys.exit(main())
