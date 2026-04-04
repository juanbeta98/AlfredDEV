# Alfred Optimization Pipeline

End-to-end pipeline for ingesting service/labor data, validating it, running an optimization algorithm, and delivering results either to an API or to local files.

This repo is structured to support both:
- API mode (fetch input from ALFRED API and send results back).
- Local mode (read CSV input and write CSV/JSON outputs for development).

---

## Quickstart

### 1) Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you use micromamba (recommended in this repo), run everything through `scripts/mrun`:
```bash
export ALFRED_MAMBA_ENV=AlfredEnv   # optional (default is AlfredEnv)
scripts/mrun python -V
scripts/mrun python -m pip install -r requirements.txt
```

### 2) Run locally with CSV input
```bash
export ALFRED_DEV_MODE=1
export USE_API=false
export LOCAL_INPUT_FILE=./data/examples/input.csv
export WRITE_MODEL_SOLUTION=true
python alfred_cli.py
```

Equivalent with micromamba:
```bash
scripts/mrun python alfred_cli.py
```

### 3) Run against API
```bash
export USE_API=true
export API_BASE_URL="https://your.api"
export API_TOKEN="your-token"
export DEPARTMENT=25
export START_DATE="2024-01-01T00:00:00"
export END_DATE="2025-12-31T23:59:59"
python alfred_cli.py
```

Equivalent with micromamba:
```bash
scripts/mrun python alfred_cli.py
```

### API Snapshot Scripts
These helpers fetch raw API responses and write JSON snapshots under `data/api_snapshots`.
Edit the filter constants at the top of each script as needed.

```bash
python scripts/api/fetch_optimization_input.py
python scripts/api/fetch_driver_directory.py
```
or
```bash
scripts/mrun python scripts/api/fetch_optimization_input.py
scripts/mrun python scripts/api/fetch_driver_directory.py
```

Optional: provide a request file (default `request.json`) to override mode and parameters.
```bash
export REQUEST_PATH=./request.json
python alfred_cli.py
```
or
```bash
export REQUEST_PATH=./request.json
scripts/mrun python alfred_cli.py
```

---

## How the Pipeline Works

The main entrypoint is `alfred_cli.py` and the pipeline runs in these stages:

1) **Config + logging bootstrap** (`src/alfred/config.py`, `src/alfred/logging_utils.py`)
2) **Request payload** (optional) (`src/alfred/io/request_loader.py`)
3) **Input acquisition**
   - API mode: `src/alfred/integration/client.py`
   - Local CSV: `src/alfred/io/input_loader.py`
4) **Parsing** API-style JSON -> DataFrame (`src/alfred/data/parsing/input_parser.py`)
5) **Validation** with rules (`src/alfred/data/validation`)
6) **Preassigned reconstruction** if `assigned_driver` exists (`src/alfred/optimization/common/preassigned.py`)
7) **Optimization solve** (`src/alfred/optimization/solver.py`)
8) **Format output payload** (`src/alfred/data/formatting/output_formatter.py`)
9) **Delivery / persistence**
   - Optional local artifacts: `src/alfred/data/io/output_writer.py`
   - Optional API POST: `src/alfred/integration/sender.py`

---

## Request Payload (Optional)

If `REQUEST_PATH` is set (defaults to `request.json`), the file can override input mode, filters, algorithm, and output path.

Example `request.json`:
```json
{
  "request_id": "sim-0001",
  "filters": {
    "department": "25",
    "start_date": "2024-01-01T00:00:00",
    "end_date": "2025-12-31T23:59:59",
    "city": "11"
  },
  "algorithm": { "name": "OFFLINE", "params": {} },
  "output": { "path": "./data/runs" }
}
```

Behavior:
- Runtime mode (`API` vs `LOCAL`) is controlled only by `USE_API` (env var).
- `filters`: optional (department, start_date, end_date, city).
- `algorithm`: name + params passed to solver.
- `output.path`: overrides output directory.

---

## Local Input Format (CSV)

`src/alfred/io/input_loader.py` expects a CSV with these required columns:
```
service_id
schedule_date
start_address_id
start_address_point
end_address_id
end_address_point
labor_id
labor_type
labor_name
labor_category
```

Location columns:
- either `city` (legacy), or
- canonical code fields: `city_code`, `department_code`
- canonical name fields: `city_name`, `department_name`

Notes:
- `start_address_point` and `end_address_point` must be WKT `POINT (lon lat)`.
- Each row represents a service + labor pairing; rows are grouped by `service_id`.
- The loader emits an API-style JSON payload for the parser.

### Build Local Inputs From Raw Files
Use these scripts to generate local inputs before running `alfred_cli.py` in `USE_API=false` mode:

```bash
python scripts/data_prep/build_input_csv_from_raw.py
python scripts/data_prep/build_driver_directory_csv.py
```

Defaults:
- Raw source directory: `data/model_input/raw_files`
- Generated service input: `data/examples/input.csv`
- Generated driver directory: `data/examples/driver_directory.csv`

---

## API Input Contract (JSON)

The parser (`src/alfred/data/parsing/input_parser.py`) expects an API-style payload like:
```json
{
  "data": [
    {
      "service_id": 1,
      "state": "scheduled",
      "created_at": "2024-02-01T12:00:00Z",
      "start_address": { "id": 10, "point": { "x": -74.1, "y": 4.6 }, "city": "149" },
      "end_address": { "id": 20, "point": { "x": -74.2, "y": 4.7 } },
      "serviceLabors": [
        {
          "id": 100,
          "labor_id": 501,
          "labor_name": "Alice",
          "labor_category": "driver",
          "schedule_date": "2024-02-02T08:00:00Z",
          "labor_sequence": 1,
          "alfred": null
        }
      ]
    }
  ]
}
```

The parser flattens this into a DataFrame with columns like:
`service_id`, `created_at`, `start_address_point`, `labor_id`, `schedule_date`, `assigned_driver`, etc.

---

## Output Formats

### API output
`src/alfred/data/formatting/output_formatter.py` builds:
```json
{
  "data": [
    {
      "service_id": 1,
      "schedule_date": "2024-02-02T08:00:00+00:00",
      "serviceLabors": [
        {
          "id": 100,
          "schedule_date": "2024-02-02T08:00:00+00:00",
          "labor_type": "driver",
          "alfred": { "id": 999 },
          "addData": {
            "distance_km": 12.5,
            "duration_min": 45.5,
            "is_infeasible": false,
            "is_warning": false,
            "infeasibility_cause_code": null,
            "warning_code": null
          }
        }
      ]
    }
  ]
}
```

- `alfred` is omitted when the labor is assigned to a shop instead of a driver.
- `addData` contains diagnostic flags and routing KPIs (values rounded to 2 decimals).

### Local output
Local artifacts are written under `RUNS_DIR` (default `./data/runs`), organized by run:

```
data/runs/run-<run_id>/
├── output/
│   ├── output.csv
│   └── output_payload.json
├── validation/                        (requires WRITE_VALIDATION_REPORTS=true)
│   ├── data_validation_invalid_rows.csv
│   ├── data_validation_report.json
│   ├── solution_validation_issues.csv
│   └── solution_validation_report.json
├── diagnostics/                       (requires WRITE_MODEL_SOLUTION=true)
│   ├── assignment_diagnostics_report.csv
│   ├── assignment_diagnostics_report.json
│   ├── solution_evaluation_report.json
│   └── preassigned_reconstruction_report.*
├── warnings.json
└── run.json
```

Flags (independent of `USE_API`):
- `WRITE_MODEL_SOLUTION=true` — writes `output/` and `diagnostics/`
- `WRITE_VALIDATION_REPORTS=true` — writes `validation/`

How to interpret solution performance metrics:
- `docs/SOLUTION_PERFORMANCE_REPORT.md`

Optional intermediate debug exports:
```bash
export WRITE_INTERMEDIATE_DATAFRAMES=true
```
When enabled, each run writes under `data/runs/run-<run_id>/intermediate/`:
- `input_df__ts-<timestamp>[__req-<request_id>].csv`
- `preassigned_df__ts-<timestamp>[__req-<request_id>].csv`
- `driver_directory__ts-<timestamp>[__req-<request_id>].csv`

`input_df` export reflects the DataFrame right before solver creation (after validation and after preassigned split).

---

## Validation Rules

Validation happens after parsing. Current default rules include:
- Required fields: `service_id`, `labor_id`, `created_at`, `schedule_date`,
  `start_address_point`, `labor_name`, `end_address_point`
- Non-empty rows
- Unique `labor_id`
- `created_at` at least 2 hours before `schedule_date`

When validation fails:
- Invalid rows are separated out.
- A summary report is generated.
- Execution aborts if all rows are invalid.

---

## Algorithms

Registered algorithms are defined in `src/alfred/optimization/algorithms/registry.py`:

| Name         | Status            | Notes |
|--------------|-------------------|-------|
| OFFLINE      | Implemented       | Baseline algorithm; supports multi-city iteration. |
| INSERT       | Implemented       | Inserts new labors into an existing preassigned schedule. |
| REACT        | Implemented       | Semi-dynamic: freezes near-term labors and re-optimizes the rest. |

Algorithm selection comes from `OptimizationSettings` or request payload:
- Default: `OFFLINE`
- Override per algorithm via `request.json` or settings overrides.

---

## Master Data

Algorithms load master data once per run from `MASTER_DATA_DIR` (default `data/master`):
- `data/master/directorio.{parquet|csv}`
- `data/master/duraciones.{parquet|csv}`
- `data/master/dist_dict.{parquet|pkl}`

`src/alfred/data/loading/master_data_loader.py` prefers parquet when available.

---

## Configuration

Environment variables (from `.env` or shell):

Core:
- `USE_API` (true/false)
- `API_BASE_URL` — base URL; `SERVICES_ENDPOINT` and `ALFREDS_ENDPOINT` are derived from it
- `SERVICES_ENDPOINT`, `ALFREDS_ENDPOINT` — override derived endpoints individually
- `API_ENDPOINT` — legacy single-endpoint fallback
- `API_TOKEN`
- `REQUEST_PATH` (default `request.json`)

Filters:
- `DEPARTMENT`
- `START_DATE` (ISO datetime)
- `END_DATE` (ISO datetime)
- `SCHEDULE_DATE` (ISO date; single-day filter)

Local mode:
- `LOCAL_INPUT_FILE` (default `./data/examples/input.csv`)
- `LOCAL_DRIVER_DIRECTORY_FILE` (default `./data/examples/driver_directory.csv`)
- `DRIVER_DIRECTORY_FALLBACK_PATH`

Execution:
- `REQUEST_TIMEOUT` (default 30s)
- `API_MAX_RETRIES` (default 3)
- `LOG_LEVEL` (default `INFO`)
- `WRITE_VALIDATION_REPORTS` (true/false)
- `WRITE_INTERMEDIATE_DATAFRAMES` (true/false)
- `WRITE_MODEL_SOLUTION` (true/false)
- `IGNORE_API_PAYLOAD_DRIVER` (true/false; strip pre-assigned drivers from API payload)

Output locations:
- `RUNS_DIR` (default `./data/runs`) — base directory for run artifacts
- `MASTER_DATA_DIR` (default `data/master`) — master data files
- `ARTIFACT_TIMEZONE` (default `America/Bogota`) — timezone for artifact timestamps

Licensing / deployment:
- `ALFRED_LICENSE` — path to the customer license file (required in production builds)
- `ALFRED_DEV_MODE` — set to any non-empty value to allow `USE_API=false` without a license

Backward compatibility:
- `EXPORT_INTERMEDIATE_DATAFRAMES` is still accepted as an alias of `WRITE_INTERMEDIATE_DATAFRAMES`.

---

## Known Behaviors and Caveats

- The solver enforces a single planning day — all input rows must share the same `schedule_date` date; inputs mixing multiple days are rejected.
- In API mode, failures attempt a best-effort error report back to the API.

---

## Customer Delivery

AlfredDEV is the internal development environment. Customer deployments use a **two-layer delivery model**.

### Layer 1 — Customer environment (one-time setup)

Run once per customer to create the persistent environment root:

```bash
./scripts/init_customer.sh --customer "Cliente S.A." --code CLIENTE \
                            --osrm-data ./osrm_resources/osrm_data/colombia
```

This creates `CLIENTE_env/` containing:
```
CLIENTE_env/
├── .env.template      ← Customer fills in credentials and license path
├── docker-compose.yml ← Orchestrates alfred + osrm services
├── osrm_data/         ← Pre-processed routing data
├── license/           ← Place the issued alfred_license.json here
├── runs/              ← Output artifacts at runtime
├── request/           ← Request file directory
├── builds/            ← Holds installed release bundles
├── app/               ← Symlink to the active build (managed by install.sh)
├── alfred_cli.py      ← CLI entry point
└── install.sh         ← Bundle installer / switcher
```

### Layer 2 — App bundle (per release)

Each release is a zip containing source code, bridges, and the `alfred_solver` binary.

#### 1. Build the binary

```bash
scripts/build_binary.sh --platform mac     # macOS ARM64 → dist/alfred_solver_mac
scripts/build_binary.sh --platform linux   # Linux amd64 → dist/alfred_solver_linux (via Docker)
scripts/build_binary.sh --platform all     # both
```

Requires `pyinstaller` in the active Python environment:
```bash
pip install pyinstaller
```

> The binary must be built on (or cross-compiled for) the target OS.

#### 2. Create a release bundle

```bash
scripts/deliver.sh --type prod --customer CLIENTE   # production (license-enforced)
scripts/deliver.sh --type dev  --customer CLIENTE   # development (no license, datetime-stamped)
```

Outputs:
- **PROD**: `builds/PROD/R<N>_CLIENTE_mac.zip` + `builds/PROD/R<N>_CLIENTE_linux.zip` + signed license
- **DEV**: `builds/DEV/RDEV_CLIENTE_YYYYMMDD_HHMM.zip`

Release IDs and binary hashes are logged to `releases/log.csv`.

#### 3. Install a bundle in the customer environment

Inside the customer's `CLIENTE_env/`:
```bash
./install.sh path/to/R1_CLIENTE_linux.zip   # extract + update app/ symlink
docker compose build alfred                  # rebuild the Docker image
```

Run `./install.sh` with no arguments for an interactive menu of available builds.

#### Dev shortcut (build + deliver + install in one step)

```bash
scripts/dev_deploy.sh --platform mac
```

Expects `ALFRED_env/` at `../ALFRED_env` (sibling of AlfredDEV). Builds binary, packages a DEV bundle, and installs it automatically.

### Licensing

License files are issued per customer using the tools in `license_tools/`. See [`license_tools/README.md`](license_tools/README.md) for the full operator guide.

The customer must set in their `.env`:
```bash
ALFRED_LICENSE=./license/alfred_license.json
```

---

## Repo Layout

```
.
├── alfred_cli.py
├── src/alfred/
│   ├── config.py
│   ├── logging_utils.py
│   ├── integration/         # API client + sender
│   ├── io/                  # request/input/output loaders
│   ├── data/                # parsing, formatting, validation
│   ├── optimization/        # solver + algorithms + settings
│   └── pipeline/            # orchestrator + check_availability
├── data/
│   ├── examples/            # sample local inputs
│   ├── runs/                # run artifacts (output, validation, diagnostics)
│   └── master/              # master data files
├── scripts/
│   ├── build_binary.sh
│   ├── deliver.sh
│   ├── init_customer.sh
│   ├── dev_deploy.sh
│   └── build_prod.sh
└── license_tools/
```

---

## Exit Codes

| Code | Source | Meaning |
|------|--------|---------|
| 0 | orchestrator / check_availability | Success |
| 1 | alfred_cli / orchestrator | Bad request file, unknown action, config or pipeline failure |
| 2 | alfred_solver binary | Input deserialization error |
| 3 | alfred_solver binary | Solver execution error |
| 4 | alfred_solver binary | Output serialization error |
| 5 | alfred_solver / license_check | License missing, invalid, expired, or tampered |

Codes 2–4 come from the internal solver binary and surface when the binary subprocess fails. Code 5 is reserved exclusively for license failures.

---

## Development Tips

- Use `request.json` to experiment with algorithm params and filters quickly.
- Set `WRITE_VALIDATION_REPORTS=true` to inspect bad rows.
- Keep master data files in `data/master` or override via `MASTER_DATA_DIR`.
- Set `ALFRED_DEV_MODE=1` to run in local mode without a license file.

---

## License

See `LICENSE`.
