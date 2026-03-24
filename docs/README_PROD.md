# Alfred

Alfred is an optimization pipeline for service scheduling. It receives a request describing a planning window, fetches the relevant services and labor data, runs an assignment algorithm, and delivers the results back to your API or writes them to local files.

---

## Setup

### Requirements

- Python 3.10+
- The `alfred_solver` binary (provided separately, placed in `bin/`)
- A valid license file (see [License](#license))

### Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment configuration

Copy or create a `.env` file (or export variables in your shell):

```bash
# Required for API mode
USE_API=true
API_ENDPOINT=https://your.api/endpoint
API_TOKEN=your-token

# Required: path to license file
ALFRED_LICENSE=./license/alfred_license.json

# Optional: override request file location (default: data/examples/request.json)
REQUEST_PATH=./request.json
```

For local (non-API) mode, use `USE_API=false` and set `LOCAL_INPUT_FILE` to a CSV input path.

---

## Running Alfred

Alfred is invoked through the unified entry point:

```bash
python alfred.py --request request.json
```

The `--request` flag can be omitted if `REQUEST_PATH` is set in the environment.

Alfred reads the `action` field in the request file and dispatches accordingly:

| Action | Description |
|--------|-------------|
| `generate_assignment` | Full optimization pipeline — fetch data, assign drivers, deliver results |
| `check_availability` | Check time slot availability for a given vehicle and route |

---

## Request Format

The request is a JSON file. The `action` field determines which workflow runs.

### generate_assignment

```json
{
  "action": "generate_assignment",
  "request_id": "run-001",
  "filters": {
    "department": "25",
    "start_date": "2026-03-23T00:00:00-05:00",
    "end_date": "2026-03-23T23:59:59-05:00"
  },
  "algorithm": {
    "name": "OFFLINE",
    "params": {
      "n_processes": -1,
      "distance_method": "osrm",
      "time_method": "osrm_times"
    }
  },
  "output": {
    "path": "./data/model_output"
  }
}
```

Fields:
- `request_id` — optional identifier included in the output payload.
- `filters` — narrows the input query (department code, date range, optionally city code).
- `algorithm.name` — always `"OFFLINE"` in this release.
- `algorithm.params` — tuning parameters passed to the solver (all optional).
- `output.path` — overrides the default local output directory (only relevant when `WRITE_MODEL_SOLUTION=true`).

### check_availability

```json
{
  "action": "check_availability",
  "department_id": 25,
  "department_name": "CUNDINAMARCA",
  "date": "2026-03-09T14:30:00-05:00",
  "license_plate": "ABC123",
  "start_address": {
    "id": 100,
    "name": "Calle 123",
    "city": "Bogotá",
    "department": "CUNDINAMARCA",
    "point": { "x": -74.1, "y": 4.6, "srid": 4326 }
  },
  "end_address": {
    "id": 200,
    "name": "Carrera 45",
    "city": "Bogotá",
    "department": "CUNDINAMARCA",
    "point": { "x": -74.2, "y": 4.7, "srid": 4326 }
  }
}
```

The result is printed as JSON to stdout.

---

## Output

### API delivery (`USE_API=true`)

When the pipeline completes, results are POSTed back to the configured API endpoint as a JSON payload:

```json
{
  "request_id": "...",
  "timestamp": "2026-03-23T10:00:00.000000",
  "status": "completed",
  "data": [ /* assigned services */ ],
  "metadata": { /* metrics */ }
}
```

### Local files (`WRITE_MODEL_SOLUTION=true`)

When enabled, output is written under `data/model_output/run-<id>/`:

| File | Contents |
|------|----------|
| `output__ts-*.csv` | Assignment results as CSV |
| `output_payload__ts-*.json` | Full JSON payload (same structure as API delivery) |

Enable with:

```bash
export WRITE_MODEL_SOLUTION=true
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Request file error, unknown action, or pipeline failure — check stderr for details |
| 5 | License invalid, expired, or not provided — see [License](#license) |

---

## License

Alfred requires a valid license file on every run. Place the license file issued for your deployment in the `license/` directory:

```
license/alfred_license.json
```

Then point Alfred to it via the environment variable:

```bash
export ALFRED_LICENSE=./license/alfred_license.json
```

Or add it to your `.env` file:

```
ALFRED_LICENSE=./license/alfred_license.json
```

If the license is missing, expired, or tampered with, Alfred exits with code `5`. Contact the Alfred team to obtain or renew a license.
