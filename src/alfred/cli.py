#!/usr/bin/env python3
"""
alfred.cli — ALFRED unified entry point.

Reads an action field from the request JSON and dispatches to the
appropriate workflow:

  action: "generate_assignment"  →  full optimization pipeline (orchestrator.py)
  action: "check_availability"   →  time slot availability checker (check_availability.py)

Usage
-----
    alfred --request request.json
    alfred --request tests/availability/request_availability.json

The --request flag can be omitted if the REQUEST_PATH environment variable
is set (falls back to "request.json" if neither is provided).
"""

import argparse
import json
import os
import sys

KNOWN_ACTIONS = {"generate_assignment", "check_availability"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ALFRED dispatcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--request",
        metavar="PATH",
        help="Path to the request JSON file (overrides REQUEST_PATH env var)",
    )
    args = parser.parse_args()

    request_path = args.request or os.environ.get("REQUEST_PATH", "request.json")

    try:
        with open(request_path, encoding="utf-8") as f:
            request = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: Failed to load request file '{request_path}': {exc}", file=sys.stderr)
        sys.exit(1)

    action = request.get("action")
    if action not in KNOWN_ACTIONS:
        known = ", ".join(sorted(KNOWN_ACTIONS))
        print(
            f"ERROR: Unknown or missing action: {action!r}. Must be one of: {known}",
            file=sys.stderr,
        )
        sys.exit(1)

    if action == "generate_assignment":
        os.environ["REQUEST_PATH"] = request_path
        from alfred.pipeline.orchestrator import main as run_assignment
        sys.exit(run_assignment())

    elif action == "check_availability":
        from alfred.pipeline.check_availability import run as run_availability
        from alfred.config import Config
        from alfred.data.io.output_writer import save_local_output_payload
        result = run_availability(request)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not Config.DISABLE_FILE_OUTPUT:
            run_id = request.get("service_id") or request.get("department_id") or None
            save_local_output_payload(result, output_dir=Config.RUNS_DIR, run_id=run_id)
        sys.exit(0)
