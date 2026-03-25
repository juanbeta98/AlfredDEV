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
import sys
from datetime import datetime
from typing import Any, Dict, Optional

from alfred.config import Config
from alfred.availability import check_availability
from alfred.availability.models import AvailabilityResponse, LaborRequest, ServiceRequest
from alfred.availability.request_parser import parse_api_request

logger = logging.getLogger(__name__)


def run(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the availability check for a pre-loaded request dict.

    Returns the serialized response dict (same shape as the JSON output).
    Raises on unrecoverable errors so the caller can handle them.
    """
    Config.validate()
    Config.configure_logging()

    department = data.get("department_name") or data.get("department_code") or None

    try:
        request = _parse_request(data)
    except (KeyError, ValueError, TypeError) as exc:
        logger.error("Invalid request format: %s", exc)
        return _error_dict(str(exc))

    try:
        response = check_availability(request)
    except Exception as exc:
        logger.exception("availability_check_failed")
        return _error_dict(str(exc))

    return _serialize_response(response, department)


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


def _serialize_response(response: AvailabilityResponse, department: Optional[str]) -> Dict[str, Any]:
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
        }}

    if not response.feasible_slots:
        return {"data": {
            "result": "occupied",
            "message": "No hay disponibilidad para este día, seleccione otra fecha",
            "department": department,
            "requested_schedule": requested,
        }}

    return {"data": {
        "result": "reschedule",
        "message": "Horario no disponible, se sugieren alternativas",
        "department": department,
        "requested_schedule": requested,
        "available_schedules": [_fmt_ts(s.slot_time) for s in response.feasible_slots],
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
