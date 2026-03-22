"""
pico_placa.py — Pico y placa (license plate traffic restriction) gate check.

Evaluates whether a vehicle's license plate is restricted from circulating
on a given date in a given department, based on rules loaded from
data/master/pico_y_placa.json.

Public API
----------
    extract_plate_digit(license_plate) -> Optional[str]
        Returns the last character of the plate if it is a digit, else None.

    is_plate_restricted(plate_digit, department_code, check_date) -> bool
        Returns True if the plate digit is blocked on that date.

The module is fail-open: any missing config, unknown department, or
unrecognised logic type results in False (plate allowed to proceed).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "master" / "pico_y_placa.json"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_pico_placa_rules(config_path: Path = _DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load and return the raw pico_y_placa config dict (result is cached)."""
    return _load_cached(str(config_path))


def extract_plate_digit(license_plate: str) -> Optional[str]:
    """
    Return the last character of license_plate if it is a digit ('0'–'9').

    Returns None if the input is empty, None, or does not end in a digit.
    The caller should treat None as "plate unreadable — skip restriction check".
    """
    if not license_plate:
        return None
    last_char = license_plate.strip()[-1] if license_plate.strip() else None
    if last_char is not None and last_char.isdigit():
        return last_char
    return None


def is_plate_restricted(
    plate_digit: str,
    department_code: str,
    check_date: date,
) -> bool:
    """
    Return True if plate_digit is restricted in department_code on check_date.

    Args:
        plate_digit     : Last digit of the license plate as a string ('0'–'9').
        department_code : Department code string, e.g. "25".
        check_date      : The date to evaluate (date object, not datetime).

    Returns:
        True  — plate is restricted; service cannot be scheduled this day.
        False — plate is not restricted (or config is missing / dept unknown).
    """
    rules_data = load_pico_placa_rules()
    departments = rules_data.get("departments", {})

    dept_config = departments.get(department_code)
    if dept_config is None:
        return False

    # Weekends are never restricted
    weekday_name = check_date.strftime("%A")
    if weekday_name in ("Saturday", "Sunday"):
        return False

    rules: List[Dict[str, Any]] = dept_config.get("rules", [])
    active_rule = _find_active_rule(rules, check_date)
    if active_rule is None:
        return False

    return _evaluate_rule(active_rule, plate_digit, check_date)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4)
def _load_cached(config_path_str: str) -> Dict[str, Any]:
    path = Path(config_path_str)
    if not path.exists():
        logger.warning(
            "pico_y_placa config not found at %s — restriction check disabled", path
        )
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    logger.info(
        "pico_y_placa_config_loaded path=%s departments=%d",
        path,
        len(data.get("departments", {})),
    )
    return data


def _find_active_rule(
    rules: List[Dict[str, Any]], check_date: date
) -> Optional[Dict[str, Any]]:
    """Return the first rule whose date range contains check_date, or None."""
    for rule in rules:
        effective_from_raw = rule.get("effective_from")
        effective_until_raw = rule.get("effective_until")

        if effective_from_raw is not None:
            if check_date < date.fromisoformat(effective_from_raw):
                continue
        if effective_until_raw is not None:
            if check_date > date.fromisoformat(effective_until_raw):
                continue
        return rule
    return None


def _evaluate_rule(
    rule: Dict[str, Any], plate_digit: str, check_date: date
) -> bool:
    """Evaluate a single active rule against the plate digit and date."""
    logic = rule.get("logic")
    restrictions: Dict[str, Any] = rule.get("restrictions", {})

    if logic == "parity":
        day_parity = "odd" if check_date.day % 2 != 0 else "even"
        blocked: List[str] = restrictions.get(day_parity, [])
        return plate_digit in blocked

    if logic == "weekday":
        weekday_name = check_date.strftime("%A")
        blocked = restrictions.get(weekday_name, [])
        return plate_digit in blocked

    logger.warning(
        "pico_y_placa unknown logic type=%r for rule — treating as unrestricted", logic
    )
    return False
