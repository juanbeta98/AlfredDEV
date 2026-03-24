"""
list_licenses.py — Audit issued license files.

Usage:
    python license_tools/list_licenses.py [--dir PATH]

Scans PATH (default: current directory) for *.json files that look like
license files. Prints a table sorted by expiry date and warns about licenses
expiring within 30 days.

Exit codes:
    0 — all licenses OK
    1 — usage error
    2 — one or more licenses are expiring within 30 days (useful for cron alerting)
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_WARN_DAYS = 30
_REQUIRED_FIELDS = {"customer", "expires", "issued_at", "signature"}


def _load_license(path: Path) -> dict | None:
    """Return parsed license dict if the file looks like a license, else None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not _REQUIRED_FIELDS.issubset(data.keys()):
        return None
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="List and audit issued license files.")
    parser.add_argument(
        "--dir", type=Path, default=Path("."),
        help="Directory to scan for *.json license files (default: current directory).",
    )
    args = parser.parse_args()

    scan_dir: Path = args.dir.resolve()
    if not scan_dir.is_dir():
        print(f"ERROR: {scan_dir} is not a directory.")
        raise SystemExit(1)

    today = date.today()
    warn_threshold = today + timedelta(days=_WARN_DAYS)

    records = []
    skipped = []
    for path in sorted(scan_dir.glob("*.json")):
        lic = _load_license(path)
        if lic is None:
            skipped.append(path.name)
            continue
        try:
            expiry = datetime.strptime(lic["expires"], "%Y-%m-%d").date()
        except ValueError:
            skipped.append(path.name)
            continue
        records.append({
            "file":      path.name,
            "customer":  lic["customer"],
            "expires":   lic["expires"],
            "issued_at": lic.get("issued_at", "?"),
            "key_id":    lic.get("key_id", ""),
            "expiry":    expiry,
        })

    records.sort(key=lambda r: r["expiry"])

    # Column widths
    cw = {"file": 30, "customer": 25, "expires": 12, "issued_at": 12, "key_id": 6}
    header = (
        f"{'File':<{cw['file']}}  "
        f"{'Customer':<{cw['customer']}}  "
        f"{'Expires':<{cw['expires']}}  "
        f"{'Issued':<{cw['issued_at']}}  "
        f"{'Key':<{cw['key_id']}}  Status"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)

    expiring_soon = []
    for r in records:
        if r["expiry"] < today:
            status = "EXPIRED"
        elif r["expiry"] <= warn_threshold:
            days_left = (r["expiry"] - today).days
            status = f"EXPIRING SOON ({days_left}d)"
            expiring_soon.append(r)
        else:
            status = "OK"

        print(
            f"{r['file']:<{cw['file']}}  "
            f"{r['customer']:<{cw['customer']}}  "
            f"{r['expires']:<{cw['expires']}}  "
            f"{r['issued_at']:<{cw['issued_at']}}  "
            f"{r['key_id']:<{cw['key_id']}}  {status}"
        )

    print()
    print(f"Total: {len(records)} license(s).")
    if skipped:
        print(f"Skipped (not license files): {', '.join(skipped)}")

    if expiring_soon:
        print(f"\nWARNING: {len(expiring_soon)} license(s) expiring within {_WARN_DAYS} days:")
        for r in expiring_soon:
            print(f"  {r['customer']!r} — expires {r['expires']} ({r['file']})")
        sys.exit(2)


if __name__ == "__main__":
    main()
