"""
Fetch per-day API snapshots (services + driver directory) for a date range,
organized under a named period folder for historic analysis.

Usage (from repo root):
    scripts/mrun python scripts/analysis/historic_fetch.py \\
        --period-name before --start-date 2025-01-06 --end-date 2025-03-31

    scripts/mrun python scripts/analysis/historic_fetch.py \\
        --period-name before --start-date 2025-01-06 --end-date 2025-03-31 --dry-run

Output layout:
    data/historic_analysis/<period-name>/
        manifest.json                         # period-level metadata
        YYYY-MM-DD/
            optimization_input_snapshot.json  # raw API services payload
            driver_directory_snapshot.json    # raw API driver list
            day_manifest.json                 # per-day metadata + status
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

def _find_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "data").exists() and (candidate / "src").exists():
            return candidate
    raise RuntimeError("Could not resolve repository root from script path.")


ROOT = _find_repo_root()
sys.path.insert(0, str(ROOT / "src"))

logger = logging.getLogger(__name__)

OUTPUT_DIR = ROOT / "data" / "historic_analysis"


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _build_exclusion_set(
    start: date,
    end: date,
    manual_excludes: list[date],
) -> dict[date, str]:
    """Return {date: reason} for all days that should be skipped."""
    excluded: dict[date, str] = {}

    for d in manual_excludes:
        excluded[d] = "manual_exclude"

    try:
        import holidays as _holidays
        years = set(range(start.year, end.year + 1))
        co_holidays = _holidays.Colombia(years=years)
        current = start
        while current <= end:
            if current in co_holidays and current not in excluded:
                excluded[current] = f"holiday: {co_holidays[current]}"
            current += timedelta(days=1)
    except ImportError:
        pass  # holidays library not installed — manual exclusion still works

    return excluded


def _date_range(start: date, end: date, excluded: dict[date, str]) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in excluded:
            days.append(current)
        current += timedelta(days=1)
    return days


def _day_dir(period_name: str, day: date) -> Path:
    return OUTPUT_DIR / period_name / day.isoformat()


def _day_is_complete(period_name: str, day: date) -> bool:
    manifest_path = _day_dir(period_name, day) / "day_manifest.json"
    if not manifest_path.exists():
        return False
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            m = json.load(f)
        return m.get("status") == "completed"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Timezone-aware service filtering
# ---------------------------------------------------------------------------

def _filter_services_by_colombia_date(services: list, target_date: date) -> list:
    """Keep only services whose schedule_date falls on target_date in Colombia time."""
    try:
        import pandas as pd
    except ImportError:
        # Fallback: parse manually using stdlib
        filtered = []
        for svc in services:
            sd = svc.get("schedule_date")
            if not sd:
                continue
            try:
                dt = datetime.fromisoformat(sd.replace("Z", "+00:00"))
                # Colombia is UTC-5 (no DST)
                col_date = (dt - timedelta(hours=5)).date()
                if col_date == target_date:
                    filtered.append(svc)
            except Exception:
                pass
        return filtered

    if not services:
        return services

    schedule_dates = pd.to_datetime(
        pd.Series([svc.get("schedule_date") for svc in services]),
        utc=True,
        errors="coerce",
    ).dt.tz_convert("America/Bogota").dt.date

    return [svc for svc, sd in zip(services, schedule_dates) if sd == target_date]


# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------

def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)


def _write_day_manifest(period_name: str, day: date, *, status: str, **extra) -> None:
    path = _day_dir(period_name, day) / "day_manifest.json"
    _write_json(path, {"date": day.isoformat(), "status": status, **extra})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch per-day snapshots for historic analysis."
    )
    parser.add_argument("--period-name", required=True, help="Label for this period, e.g. 'before' or 'after'.")
    parser.add_argument("--start-date", required=True, help="Start date inclusive, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="End date inclusive, YYYY-MM-DD.")
    parser.add_argument("--department", type=int, default=None, help="Department code filter (optional).")
    parser.add_argument(
        "--exclude-dates",
        nargs="*",
        default=[],
        metavar="YYYY-MM-DD",
        help="Specific dates to skip (e.g. public holidays). Repeatable or space-separated.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan without calling the API.")
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if end_date < start_date:
        print("ERROR: --end-date must be >= --start-date", file=sys.stderr)
        return 1

    manual_excludes = [date.fromisoformat(d) for d in (args.exclude_dates or [])]
    excluded = _build_exclusion_set(start_date, end_date, manual_excludes)
    days = _date_range(start_date, end_date, excluded)

    if args.dry_run:
        already_done = [d for d in days if _day_is_complete(args.period_name, d)]
        to_fetch = [d for d in days if not _day_is_complete(args.period_name, d)]
        print(json.dumps({
            "period_name": args.period_name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "department": args.department,
            "total_days": len(days),
            "already_completed": [d.isoformat() for d in already_done],
            "to_fetch": [d.isoformat() for d in to_fetch],
            "excluded": {d.isoformat(): reason for d, reason in excluded.items()},
            "output_dir": (OUTPUT_DIR / args.period_name).as_posix(),
        }, indent=2))
        return 0

    from alfred.config import Config
    from alfred.data.api.client import ALFREDAPIClient
    from alfred.utils.logging_utils import setup_logging_context

    setup_logging_context()
    Config.configure_logging()

    if not Config.SERVICES_ENDPOINT:
        print("ERROR: SERVICES_ENDPOINT (or API_BASE_URL/API_ENDPOINT) is not set.", file=sys.stderr)
        return 1
    if not Config.ALFREDS_ENDPOINT:
        print("ERROR: ALFREDS_ENDPOINT is not set.", file=sys.stderr)
        return 1
    if not Config.API_TOKEN:
        print("ERROR: API_TOKEN is not set.", file=sys.stderr)
        return 1

    client = ALFREDAPIClient(
        endpoint_url=Config.SERVICES_ENDPOINT,
        api_token=Config.API_TOKEN,
        timeout=Config.REQUEST_TIMEOUT,
        max_retries=Config.API_MAX_RETRIES,
    )
    # Driver directory uses the same base credentials but a different endpoint
    driver_client = ALFREDAPIClient(
        endpoint_url=Config.ALFREDS_ENDPOINT,
        api_token=Config.API_TOKEN,
        timeout=Config.REQUEST_TIMEOUT,
        max_retries=Config.API_MAX_RETRIES,
    )

    days_completed: list[str] = []
    days_failed: list[str] = []
    days_skipped: list[str] = []

    for day in days:
        day_str = day.isoformat()

        if _day_is_complete(args.period_name, day):
            logger.info("skipping_completed_day date=%s", day_str)
            days_skipped.append(day_str)
            days_completed.append(day_str)
            continue

        logger.info("fetching_day date=%s department=%s", day_str, args.department)

        try:
            # --- Services ---
            day_start = datetime(day.year, day.month, day.day, 0, 0, 0)
            day_end = datetime(day.year, day.month, day.day, 23, 59, 59)

            raw_services = client.get_optimization_data(
                department=args.department,
                start_date=day_start,
                end_date=day_end,
            )

            all_services = raw_services.get("data", []) if isinstance(raw_services, dict) else []
            services_fetched = len(all_services)

            filtered_services = _filter_services_by_colombia_date(all_services, day)
            services_after_filter = len(filtered_services)

            filtered_payload = dict(raw_services) if isinstance(raw_services, dict) else {}
            filtered_payload["data"] = filtered_services

            services_path = _day_dir(args.period_name, day) / "optimization_input_snapshot.json"
            _write_json(services_path, filtered_payload)
            logger.info(
                "services_snapshot_written date=%s services_fetched=%d services_kept=%d path=%s",
                day_str, services_fetched, services_after_filter, services_path.as_posix(),
            )

            # --- Drivers ---
            raw_drivers = driver_client.get_driver_directory(
                active=True,
                schedule_date=day,
                department=args.department,
            )

            drivers_fetched = len(raw_drivers) if isinstance(raw_drivers, list) else (
                len(raw_drivers.get("data", [])) if isinstance(raw_drivers, dict) else 0
            )

            drivers_path = _day_dir(args.period_name, day) / "driver_directory_snapshot.json"
            _write_json(drivers_path, raw_drivers)
            logger.info(
                "drivers_snapshot_written date=%s drivers_fetched=%d path=%s",
                day_str, drivers_fetched, drivers_path.as_posix(),
            )

            # --- Day manifest ---
            _write_day_manifest(
                args.period_name, day,
                status="completed",
                services_fetched=services_fetched,
                services_after_filter=services_after_filter,
                drivers_fetched=drivers_fetched,
            )
            days_completed.append(day_str)
            print(f"  [OK] {day_str}: {services_after_filter} services, {drivers_fetched} drivers", flush=True)

        except Exception as exc:
            logger.error("fetch_day_failed date=%s error=%s", day_str, exc)
            traceback.print_exc()
            _write_day_manifest(args.period_name, day, status="failed", error=str(exc))
            days_failed.append(day_str)
            print(f"  [FAIL] {day_str}: {exc}", flush=True)

    # --- Period manifest ---
    period_manifest = {
        "period_name": args.period_name,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "department": args.department,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "total_days": len(days),
        "days_completed": days_completed,
        "days_failed": days_failed,
        "days_skipped_already_done": days_skipped,
        "days_excluded": {d.isoformat(): reason for d, reason in excluded.items()},
        "status": "completed" if not days_failed else "partial",
    }
    period_manifest_path = OUTPUT_DIR / args.period_name / "manifest.json"
    _write_json(period_manifest_path, period_manifest)
    logger.info("period_manifest_written path=%s", period_manifest_path.as_posix())

    print(
        f"\nDone. {len(days_completed)}/{len(days)} days completed, "
        f"{len(days_failed)} failed. Manifest: {period_manifest_path.as_posix()}",
        flush=True,
    )
    return 0 if not days_failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
