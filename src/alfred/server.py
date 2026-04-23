"""
alfred.server — HTTP API wrapper around the Alfred CLI.

Exposes the Alfred optimizer over HTTP so callers can POST a request JSON body
and receive the result without managing request.json files directly.

Endpoints
---------
GET  /health   Liveness check.
POST /run      Run an Alfred request. Body is the same JSON accepted by the CLI.

Response (POST /run)
--------------------
{
  "exit_code": int,          # 0 = success, 1 = error, 5 = license failure
  "stdout": str,             # CLI stdout (check_availability result JSON is here)
  "stderr": str,             # CLI stderr (logs, error messages)
  "elapsed_seconds": float,  # Wall-clock time for the run
  "result": object | null    # Parsed stdout JSON for check_availability; null otherwise
}

Usage
-----
    uvicorn alfred.server:app --host 0.0.0.0 --port 8000
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from alfred.config import Config

app = FastAPI(title="Alfred API", version="1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/run")
async def run(request: Request) -> JSONResponse:
    body: Any = await request.json()

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        json.dump(body, tmp, ensure_ascii=False)
        tmp_path = tmp.name

    try:
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                ["alfred", "--request", tmp_path],
                capture_output=True,
                text=True,
                timeout=Config.SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - t0
            return JSONResponse(
                content={
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"alfred subprocess timed out after {Config.SUBPROCESS_TIMEOUT}s",
                    "elapsed_seconds": round(elapsed, 3),
                    "result": None,
                }
            )
        elapsed = time.monotonic() - t0

        # Forward subprocess output to both streams so Cloud Logging captures it
        # regardless of which stream the customer's environment reads.
        # [ALFRED-LOG] = pipeline log lines, [ALFRED-OUT] = structured output.
        if proc.stderr:
            for line in proc.stderr.splitlines():
                print(f"[ALFRED-LOG] {line}", flush=True)
                print(f"[ALFRED-LOG] {line}", file=sys.stderr, flush=True)
        if proc.stdout:
            for line in proc.stdout.splitlines():
                print(f"[ALFRED-OUT] {line}", flush=True)
                print(f"[ALFRED-OUT] {line}", file=sys.stderr, flush=True)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    # For check_availability the CLI prints the result JSON to stdout.
    # Parse it so callers get a proper object instead of a raw string.
    result = None
    action = body.get("action") if isinstance(body, dict) else None
    if action == "check_availability" and proc.returncode == 0 and stdout.strip():
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            pass

    return JSONResponse(
        content={
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_seconds": round(elapsed, 3),
            "result": result,
        }
    )
