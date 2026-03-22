"""
validate_deployment.py — AlfredProd deployment sanity checker.

Run this after deploying AlfredProd to confirm the environment is correctly
configured before accepting real traffic.

Usage:
    python validate_deployment.py [--repo-dir PATH] [--binary PATH] [--license PATH]

Options:
    --repo-dir PATH   Root of the AlfredProd directory. Default: directory
                      containing this script.
    --binary PATH     Override path to the alfred_solver binary.
                      Default: <repo-dir>/bin/alfred_solver
    --license PATH    Override path to the license file.
                      Default: $ALFRED_LICENSE environment variable.

Exit codes:
    0   All checks passed.
    1   One or more checks failed (details printed to stdout).

Checks performed:
    1. Binary exists and is executable.
    2. License file exists.
    3. License is valid and not expired (warns if expiring within 30 days).
    4. Binary health check passes (--mode health).
    5. Python bridge imports work (SolverBridge, run_insertion_worker_bridge).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_WARN_DAYS = 30


def _check(label: str, ok: bool, detail: str = "") -> bool:
    """Print a single check result line. Returns ok."""
    status = "OK  " if ok else "FAIL"
    line = f"  [{status}]  {label}"
    if detail:
        line += f": {detail}"
    print(line)
    return ok


def _run_checks(repo_dir: Path, binary: Path, license_path: str) -> bool:
    """Run all checks. Returns True if all passed."""
    print("=== AlfredProd Deployment Validation ===")
    print(f"  Repo    : {repo_dir}")
    print(f"  Binary  : {binary}")
    print(f"  License : {license_path or '(not set)'}")
    print()

    all_ok = True

    # ------------------------------------------------------------------
    # Check 1: Binary exists and is executable
    # ------------------------------------------------------------------
    bin_ok = binary.exists() and os.access(binary, os.X_OK)
    if not _check(
        "Binary exists and is executable",
        bin_ok,
        str(binary) if bin_ok else f"not found or not executable at {binary}",
    ):
        all_ok = False

    # ------------------------------------------------------------------
    # Check 2: License file exists
    # ------------------------------------------------------------------
    if not license_path:
        _check("License file", False, "ALFRED_LICENSE not set and --license not provided")
        all_ok = False
        lic_ok = False
    else:
        lic_file = Path(license_path)
        lic_ok = lic_file.exists()
        if not _check(
            "License file exists",
            lic_ok,
            license_path if lic_ok else f"not found at {license_path}",
        ):
            all_ok = False

    # ------------------------------------------------------------------
    # Check 3: License validity and expiry
    # ------------------------------------------------------------------
    if lic_ok:
        try:
            data = json.loads(Path(license_path).read_text(encoding="utf-8"))
            customer = data.get("customer", "?")
            expires_str = data.get("expires", "")
            expiry = datetime.strptime(expires_str, "%Y-%m-%d").date()
            today = date.today()
            days_left = (expiry - today).days

            if today > expiry:
                _check("License validity", False, f"EXPIRED on {expires_str} (customer={customer!r})")
                all_ok = False
            elif days_left <= _WARN_DAYS:
                # Warn but don't fail — expired is a fail, near-expiry is a warning
                print(
                    f"  [WARN]  License expiring soon: {expires_str} "
                    f"({days_left} days) — customer={customer!r}"
                )
            else:
                _check(
                    "License valid",
                    True,
                    f"customer={customer!r} expires={expires_str} ({days_left} days)",
                )
        except Exception as exc:
            _check("License validity", False, f"could not parse license file: {exc}")
            all_ok = False

    # ------------------------------------------------------------------
    # Check 4: Binary health check
    # ------------------------------------------------------------------
    health_ok = False
    health_detail = ""
    if bin_ok and lic_ok:
        try:
            result = subprocess.run(
                [str(binary), "--mode", "health", "--license", license_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                try:
                    health_data = json.loads(result.stdout.strip())
                    serde_v = health_data.get("serde_version", "?")
                    solver_v = health_data.get("solver_version", "?")
                    health_detail = f"serde_version={serde_v}, solver_version={solver_v}"
                    health_ok = True
                except json.JSONDecodeError:
                    health_detail = f"binary returned non-JSON stdout: {result.stdout.strip()!r}"
            elif result.returncode == 5:
                health_detail = "license rejected by binary (exit 5)"
            else:
                stderr_preview = result.stderr.strip().splitlines()
                health_detail = (
                    f"exit {result.returncode}"
                    + (f" — {stderr_preview[0]}" if stderr_preview else "")
                )
        except subprocess.TimeoutExpired:
            health_detail = "binary timed out after 30s"
        except Exception as exc:
            health_detail = f"failed to run binary: {exc}"
    elif not bin_ok:
        health_detail = "skipped (binary not available)"
    else:
        health_detail = "skipped (license not available)"

    if not _check("Binary health check", health_ok, health_detail):
        if bin_ok and lic_ok:
            all_ok = False

    # ------------------------------------------------------------------
    # Check 5: Python bridge imports
    # ------------------------------------------------------------------
    # Add repo root to sys.path so imports work regardless of working directory

    imports_ok = True
    imported = []
    failed_imports = []

    for module, name in [
        ("alfred.optimization.solver_bridge", "SolverBridge"),
        ("alfred.availability.probe_bridge", "run_insertion_worker_bridge"),
    ]:
        try:
            mod = __import__(module, fromlist=[name])
            getattr(mod, name)
            imported.append(name)
        except Exception as exc:
            failed_imports.append(f"{name} ({exc})")
            imports_ok = False

    if imports_ok:
        _check("Python bridge imports", True, ", ".join(imported))
    else:
        _check(
            "Python bridge imports",
            False,
            "failed: " + "; ".join(failed_imports),
        )
        all_ok = False

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    if all_ok:
        print("=== All checks passed ===")
    else:
        print("=== DEPLOYMENT VALIDATION FAILED — fix the issues above before running ===")

    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate an AlfredProd deployment before running."
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Root of the AlfredProd directory (default: directory of this script).",
    )
    parser.add_argument(
        "--binary",
        type=Path,
        default=None,
        help="Override path to alfred_solver binary (default: <repo-dir>/bin/alfred_solver).",
    )
    parser.add_argument(
        "--license",
        default=os.environ.get("ALFRED_LICENSE"),
        help="Override license file path (default: $ALFRED_LICENSE).",
    )
    args = parser.parse_args()

    repo_dir = args.repo_dir.resolve()
    suffix = ".exe" if os.name == "nt" else ""
    binary = args.binary.resolve() if args.binary else repo_dir / "bin" / f"alfred_solver{suffix}"

    all_ok = _run_checks(repo_dir, binary, args.license)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
