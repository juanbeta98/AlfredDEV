"""
serde.py — Serialization/deserialization contract for the alfred_solver executable.

This module is the single shared contract between:
  - The solver bridge (AlfredProd: src/optimization/solver_bridge.py)
  - The probe bridge  (AlfredProd: src/availability/probe_bridge.py)
  - The executable entry point (solver_executable/main.py)

Design principles:
  - No imports from alfred.optimization.algorithms or src.optimization.solver.
    Only settings/data dataclasses, pandas, and pyarrow are imported.
  - DataFrames are serialized as Parquet (preserves dtypes including tz-aware datetimes).
  - dist_dict uses the canonical [city, p1, p2, distance_km] Parquet layout
    (same format as master_data_loader._load_dist_dict).
  - All scalars travel in a JSON envelope alongside file paths.
  - probe_params (scalar kwargs for run_insertion_worker) are inlined in the
    JSON envelope so the probe bridge never needs to reconstruct OptimizationSettings.

Solve mode file layout (tmpdir/):
  solver_input.json       — mode, settings dict, context scalars, file paths
  input_df.parquet
  directorio_df.parquet
  duraciones_df.parquet
  dist_dict.parquet       — [city, p1, p2, distance_km]
  preassigned_labors.parquet
  preassigned_moves.parquet

  solver_output.json      — metrics dict, algo_artifacts scalars, file paths
  results_df.parquet
  moves_df.parquet
  dist_dict_out.parquet   — updated distance cache (written only when dist changed)

Probe mode file layout (tmpdir/):
  probe_input.json        — mode, probe_params scalars, file paths
  base_labors.parquet
  base_moves.parquet
  candidate.parquet
  dist_dict.parquet
  duraciones.parquet
  directorio.parquet

  probe_output.json       — {"num_inserted": N} + file paths
  probe_result.parquet    — result_df from run_insertion_worker
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Serde contract version
# ---------------------------------------------------------------------------
# Bump this integer whenever the JSON envelope schema changes (new required
# fields, removed fields, changed semantics).  Both the bridge and the binary
# must agree on SERDE_VERSION — a mismatch raises ValueError at deserialization
# time, making out-of-sync deploys immediately visible.
SERDE_VERSION = 1


def _check_version(envelope: dict, context: str) -> None:
    """Raise ValueError if the envelope's serde_version doesn't match SERDE_VERSION."""
    v = envelope.get("serde_version")
    if v != SERDE_VERSION:
        raise ValueError(
            f"serde version mismatch in {context}: "
            f"expected {SERDE_VERSION}, got {v!r}. "
            "Bridge and binary may be out of sync — rebuild or redeploy."
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write a DataFrame to Parquet, creating an empty-schema file for empty DFs."""
    df.to_parquet(path, index=True, engine="pyarrow")


def _read_parquet(path: Path) -> pd.DataFrame:
    try:
        return pd.read_parquet(path, engine="pyarrow")
    except Exception as exc:
        raise ValueError(f"Failed to read Parquet file {path}: {exc}") from exc


def _dist_dict_to_df(dist_dict: Dict[str, Dict[Tuple[str, str], Any]]) -> pd.DataFrame:
    """Convert dist_dict → canonical [city, p1, p2, distance_km] DataFrame."""
    rows: List[Tuple[str, str, str, Any]] = []
    for city_key, city_dist in dist_dict.items():
        if not isinstance(city_dist, dict):
            continue
        for (p1, p2), dist in city_dist.items():
            rows.append((str(city_key), str(p1), str(p2), dist))
    return pd.DataFrame(rows, columns=["city", "p1", "p2", "distance_km"])


def _df_to_dist_dict(df: pd.DataFrame) -> Dict[str, Dict[Tuple[str, str], Any]]:
    """Reconstruct dist_dict from canonical [city, p1, p2, distance_km] DataFrame."""
    dist_dict: Dict[str, Dict[Tuple[str, str], Any]] = {}
    for city, p1, p2, dist in df[["city", "p1", "p2", "distance_km"]].itertuples(index=False):
        dist_dict.setdefault(str(city), {})[(str(p1), str(p2))] = dist
    return dist_dict


def _time_dict_to_df(time_dict: Dict[Tuple[str, str], Any]) -> pd.DataFrame:
    """Convert flat time_dict {(p1, p2): minutes} → DataFrame [p1, p2, time_min]."""
    rows: List[Tuple[str, str, Any]] = []
    for (p1, p2), t in time_dict.items():
        rows.append((str(p1), str(p2), t))
    return pd.DataFrame(rows, columns=["p1", "p2", "time_min"])


def _df_to_time_dict(df: pd.DataFrame) -> Dict[Tuple[str, str], Any]:
    """Reconstruct time_dict from DataFrame [p1, p2, time_min]."""
    result: Dict[Tuple[str, str], Any] = {}
    for p1, p2, t in df[["p1", "p2", "time_min"]].itertuples(index=False):
        result[(str(p1), str(p2))] = t
    return result


def _settings_to_dict(settings) -> Dict[str, Any]:
    """Serialize OptimizationSettings to a plain dict (JSON-safe)."""
    d = dataclasses.asdict(settings)
    # model_params and master_data are nested frozen dataclasses; asdict handles them.
    return d


def _settings_from_dict(d: Dict[str, Any]):
    """Reconstruct OptimizationSettings from a plain dict."""
    # Import here so this module stays importable without the full src tree
    # when running inside the binary (all src.* imports are bundled).
    from alfred.optimization.settings.model_params import ModelParams
    from alfred.optimization.settings.master_data import MasterDataParams
    from alfred.optimization.settings.solver_settings import OptimizationSettings

    mp_dict = d.pop("model_params", {})
    md_dict = d.pop("master_data", {})
    model_params = ModelParams(**mp_dict)
    master_data = MasterDataParams(**md_dict)
    return OptimizationSettings(model_params=model_params, master_data=master_data, **d)


def _context_to_serializable(context: Dict[str, Any]) -> Dict[str, Any]:
    """Make context JSON-safe (pd.Timestamp → isoformat, strip non-scalar keys)."""
    out: Dict[str, Any] = {}
    for k, v in context.items():
        if k in ("preassigned", "master_data"):
            # These are not plain scalars — skip; the bridge reconstructs them.
            continue
        if isinstance(v, pd.Timestamp):
            out[k] = v.isoformat()
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            try:
                json.dumps(v)
                out[k] = v
            except (TypeError, ValueError):
                logger.warning(
                    "serde: dropping non-JSON-serializable context key %r "
                    "— it will not be available in the binary",
                    k,
                )
    return out


def _context_from_serializable(d: Dict[str, Any]) -> Dict[str, Any]:
    """Reconstruct context from serialized form (isoformat strings → pd.Timestamp)."""
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if k == "decision_time" and v is not None:
            out[k] = pd.Timestamp(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Solve mode — serialize input
# ---------------------------------------------------------------------------

def serialize_solver_input(
    tmpdir: Path,
    input_df: pd.DataFrame,
    settings,
    context: Dict[str, Any],
    master_data,
) -> Path:
    """
    Write solver inputs to tmpdir.  Returns path to solver_input.json.

    Args:
        tmpdir      : Temp directory (must exist).
        input_df    : Labors DataFrame.
        settings    : OptimizationSettings instance.
        context     : Solver context dict (may include preassigned, decision_time).
        master_data : MasterData(directorio_df, duraciones_df, dist_dict).

    Returns:
        Path to solver_input.json (passed as argv[1] to the executable).
    """
    tmpdir = Path(tmpdir)

    # DataFrames
    _write_parquet(input_df, tmpdir / "input_df.parquet")
    _write_parquet(master_data.directorio_df, tmpdir / "directorio_df.parquet")
    _write_parquet(master_data.duraciones_df, tmpdir / "duraciones_df.parquet")
    _write_parquet(_dist_dict_to_df(master_data.dist_dict), tmpdir / "dist_dict.parquet")

    # Preassigned state (may be empty)
    preassigned = context.get("preassigned", {})
    preassigned_labors = preassigned.get("labors_df", pd.DataFrame())
    preassigned_moves = preassigned.get("moves_df", pd.DataFrame())
    _write_parquet(preassigned_labors, tmpdir / "preassigned_labors.parquet")
    _write_parquet(preassigned_moves, tmpdir / "preassigned_moves.parquet")

    # JSON envelope
    envelope = {
        "serde_version": SERDE_VERSION,
        "mode": "solve",
        "settings": _settings_to_dict(settings),
        "context": _context_to_serializable(context),
        "files": {
            "input_df": "input_df.parquet",
            "directorio_df": "directorio_df.parquet",
            "duraciones_df": "duraciones_df.parquet",
            "dist_dict": "dist_dict.parquet",
            "preassigned_labors": "preassigned_labors.parquet",
            "preassigned_moves": "preassigned_moves.parquet",
        },
    }

    input_json = tmpdir / "solver_input.json"
    input_json.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    return input_json


# ---------------------------------------------------------------------------
# Solve mode — deserialize input
# ---------------------------------------------------------------------------

def deserialize_solver_input(
    input_json_path: Path,
) -> Tuple[pd.DataFrame, Any, Dict[str, Any], Any]:
    """
    Read solver inputs written by serialize_solver_input.

    Returns:
        (input_df, settings, context, master_data)
    """
    from alfred.data.loading.master_data_loader import MasterData

    input_json_path = Path(input_json_path)
    envelope = json.loads(input_json_path.read_text(encoding="utf-8"))
    _check_version(envelope, "solver_input")
    tmpdir = input_json_path.parent
    files = envelope["files"]

    input_df = _read_parquet(tmpdir / files["input_df"])
    directorio_df = _read_parquet(tmpdir / files["directorio_df"])
    duraciones_df = _read_parquet(tmpdir / files["duraciones_df"])
    dist_dict = _df_to_dist_dict(_read_parquet(tmpdir / files["dist_dict"]))

    preassigned_labors = _read_parquet(tmpdir / files["preassigned_labors"])
    preassigned_moves = _read_parquet(tmpdir / files["preassigned_moves"])

    settings = _settings_from_dict(dict(envelope["settings"]))
    context = _context_from_serializable(envelope.get("context", {}))

    # Reconstruct preassigned sub-dict inside context
    if not preassigned_labors.empty or not preassigned_moves.empty:
        context["preassigned"] = {
            "labors_df": preassigned_labors,
            "moves_df": preassigned_moves,
        }

    master_data = MasterData(
        directorio_df=directorio_df,
        duraciones_df=duraciones_df,
        dist_dict=dist_dict,
    )

    return input_df, settings, context, master_data


# ---------------------------------------------------------------------------
# Solve mode — serialize output
# ---------------------------------------------------------------------------

def serialize_solver_output(
    tmpdir: Path,
    results_df: pd.DataFrame,
    metrics: Dict[str, Any],
    algo_artifacts: Dict[str, Any],
) -> Path:
    """
    Write solver outputs to tmpdir.  Returns path to solver_output.json.

    Args:
        tmpdir         : Temp directory (must exist).
        results_df     : Assigned labors DataFrame.
        metrics        : Solver metrics dict (all JSON-safe scalars).
        algo_artifacts : Algorithm artifacts dict (may contain DataFrames).

    Returns:
        Path to solver_output.json (printed to stdout by the executable).
    """
    tmpdir = Path(tmpdir)

    _write_parquet(results_df, tmpdir / "results_df.parquet")

    moves_df = algo_artifacts.get("moves_df", pd.DataFrame())
    if moves_df is None:
        moves_df = pd.DataFrame()
    _write_parquet(moves_df, tmpdir / "moves_df.parquet")

    # dist_dict — write only when present (may have been updated by algorithm)
    dist_dict_out = algo_artifacts.get("dist_dict")
    has_dist_dict_out = isinstance(dist_dict_out, dict) and bool(dist_dict_out)
    if has_dist_dict_out:
        _write_parquet(_dist_dict_to_df(dist_dict_out), tmpdir / "dist_dict_out.parquet")

    # Scalar artifacts (strip non-JSON-safe items: DataFrames, dicts-of-dicts)
    scalar_artifacts: Dict[str, Any] = {}
    for k, v in algo_artifacts.items():
        if k in ("moves_df", "dist_dict"):
            continue
        if isinstance(v, pd.DataFrame):
            # Serialize unexpected DataFrames alongside
            pq_path = tmpdir / f"artifact_{k}.parquet"
            _write_parquet(v, pq_path)
            scalar_artifacts[f"__parquet_{k}"] = f"artifact_{k}.parquet"
            continue
        try:
            json.dumps(v, default=str)
            scalar_artifacts[k] = v
        except (TypeError, ValueError):
            logger.debug("serde: skipping non-serializable artifact key %r", k)

    envelope = {
        "serde_version": SERDE_VERSION,
        "mode": "solve",
        "metrics": metrics,
        "algo_artifacts": scalar_artifacts,
        "has_dist_dict_out": has_dist_dict_out,
        "files": {
            "results_df": "results_df.parquet",
            "moves_df": "moves_df.parquet",
            **({"dist_dict_out": "dist_dict_out.parquet"} if has_dist_dict_out else {}),
        },
    }

    output_json = tmpdir / "solver_output.json"
    output_json.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    return output_json


# ---------------------------------------------------------------------------
# Solve mode — deserialize output
# ---------------------------------------------------------------------------

def deserialize_solver_output(
    output_json_path: Path,
) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Read solver outputs written by serialize_solver_output.

    Returns:
        (results_df, metrics, algo_artifacts)
    """
    output_json_path = Path(output_json_path)
    envelope = json.loads(output_json_path.read_text(encoding="utf-8"))
    _check_version(envelope, "solver_output")
    tmpdir = output_json_path.parent
    files = envelope["files"]

    results_df = _read_parquet(tmpdir / files["results_df"])
    moves_df = _read_parquet(tmpdir / files["moves_df"])
    metrics = envelope["metrics"]

    algo_artifacts: Dict[str, Any] = dict(envelope.get("algo_artifacts", {}))
    algo_artifacts["moves_df"] = moves_df

    if envelope.get("has_dist_dict_out"):
        algo_artifacts["dist_dict"] = _df_to_dist_dict(
            _read_parquet(tmpdir / files["dist_dict_out"])
        )

    # Reconstruct any extra DataFrames that were serialized
    parquet_keys = [k for k in list(algo_artifacts.keys()) if k.startswith("__parquet_")]
    for pkey in parquet_keys:
        original_key = pkey[len("__parquet_"):]
        fname = algo_artifacts.pop(pkey)
        fpath = tmpdir / fname
        if fpath.exists():
            algo_artifacts[original_key] = _read_parquet(fpath)

    return results_df, metrics, algo_artifacts


# ---------------------------------------------------------------------------
# Probe mode — serialize input
# ---------------------------------------------------------------------------

def serialize_probe_input(
    tmpdir: Path,
    *,
    base_labors_df: pd.DataFrame,
    base_moves_df: pd.DataFrame,
    new_labors_df: pd.DataFrame,
    seed: int,
    city: str,
    fecha: str,
    directorio_df: pd.DataFrame,
    drivers: List[str],
    dist_dict: Dict[Any, Any],
    distance_method: str,
    alfred_speed: float,
    vehicle_transport_speed: float,
    tiempo_alistar: float,
    tiempo_finalizacion: float,
    tiempo_gracia: float,
    early_buffer: float,
    workday_end_dt,
    duraciones_df: Optional[pd.DataFrame] = None,
    time_method: str = "speed_based",
    time_dict: Optional[Dict[Any, Any]] = None,
) -> Path:
    """
    Write insertion-probe inputs to tmpdir.  Returns path to probe_input.json.

    The kwargs mirror the signature of run_insertion_worker exactly so the
    probe bridge can call this with **kwargs after assembling the arguments.
    """
    tmpdir = Path(tmpdir)

    _write_parquet(base_labors_df, tmpdir / "base_labors.parquet")
    _write_parquet(base_moves_df, tmpdir / "base_moves.parquet")
    _write_parquet(new_labors_df, tmpdir / "candidate.parquet")
    _write_parquet(directorio_df, tmpdir / "directorio.parquet")

    if duraciones_df is not None and not duraciones_df.empty:
        _write_parquet(duraciones_df, tmpdir / "duraciones.parquet")
        has_duraciones = True
    else:
        has_duraciones = False

    # dist_dict for this probe is already city-sliced (a flat dict of (p1,p2)->dist)
    # Wrap it so _dist_dict_to_df can handle it.
    wrapped_dist = {str(city): dist_dict} if dist_dict else {}
    _write_parquet(_dist_dict_to_df(wrapped_dist), tmpdir / "dist_dict.parquet")

    # time_dict has tuple keys — cannot be inlined in JSON; write to Parquet instead.
    has_time_dict = bool(time_dict)
    if has_time_dict:
        _write_parquet(_time_dict_to_df(time_dict), tmpdir / "time_dict.parquet")

    # workday_end_dt: pd.Timestamp or datetime → isoformat string
    workday_end_str = None
    if workday_end_dt is not None:
        workday_end_str = pd.Timestamp(workday_end_dt).isoformat()

    probe_params = {
        "seed": seed,
        "city": city,
        "fecha": fecha,
        "drivers": drivers,
        "distance_method": distance_method,
        "alfred_speed": alfred_speed,
        "vehicle_transport_speed": vehicle_transport_speed,
        "tiempo_alistar": tiempo_alistar,
        "tiempo_finalizacion": tiempo_finalizacion,
        "tiempo_gracia": tiempo_gracia,
        "early_buffer": early_buffer,
        "workday_end_dt_iso": workday_end_str,
        "time_method": time_method,
    }

    envelope = {
        "serde_version": SERDE_VERSION,
        "mode": "probe",
        "probe_params": probe_params,
        "has_duraciones": has_duraciones,
        "files": {
            "base_labors": "base_labors.parquet",
            "base_moves": "base_moves.parquet",
            "candidate": "candidate.parquet",
            "directorio": "directorio.parquet",
            "dist_dict": "dist_dict.parquet",
            **({"duraciones": "duraciones.parquet"} if has_duraciones else {}),
            **({"time_dict": "time_dict.parquet"} if has_time_dict else {}),
        },
    }

    input_json = tmpdir / "probe_input.json"
    input_json.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    return input_json


# ---------------------------------------------------------------------------
# Probe mode — deserialize input
# ---------------------------------------------------------------------------

def deserialize_probe_input(
    input_json_path: Path,
) -> Dict[str, Any]:
    """
    Read probe inputs written by serialize_probe_input.

    Returns a dict of kwargs ready to be unpacked into run_insertion_worker(**kwargs).
    """
    input_json_path = Path(input_json_path)
    envelope = json.loads(input_json_path.read_text(encoding="utf-8"))
    _check_version(envelope, "probe_input")
    tmpdir = input_json_path.parent
    files = envelope["files"]
    pp = envelope["probe_params"]

    base_labors_df = _read_parquet(tmpdir / files["base_labors"])
    base_moves_df = _read_parquet(tmpdir / files["base_moves"])
    new_labors_df = _read_parquet(tmpdir / files["candidate"])
    directorio_df = _read_parquet(tmpdir / files["directorio"])

    dist_df = _read_parquet(tmpdir / files["dist_dict"])
    full_dist = _df_to_dist_dict(dist_df)
    # Unwrap from the city wrapper (probe uses flat city-slice dict)
    city = str(pp["city"])
    dist_dict = full_dist.get(city)
    if dist_dict is None:
        raise ValueError(
            f"dist_dict deserialization error: city {city!r} not found in serialized "
            f"dist_dict. Available keys: {list(full_dist.keys())}"
        )

    duraciones_df: Optional[pd.DataFrame] = None
    if envelope.get("has_duraciones") and "duraciones" in files:
        duraciones_df = _read_parquet(tmpdir / files["duraciones"])

    # Reconstruct workday_end_dt
    workday_end_dt = None
    if pp.get("workday_end_dt_iso"):
        workday_end_dt = pd.Timestamp(pp["workday_end_dt_iso"])

    return {
        "base_labors_df": base_labors_df,
        "base_moves_df": base_moves_df,
        "new_labors_df": new_labors_df,
        "seed": int(pp["seed"]),
        "city": city,
        "fecha": str(pp["fecha"]),
        "directorio_df": directorio_df,
        "drivers": list(pp["drivers"]),
        "dist_dict": dist_dict,
        "distance_method": str(pp["distance_method"]),
        "alfred_speed": float(pp["alfred_speed"]),
        "vehicle_transport_speed": float(pp["vehicle_transport_speed"]),
        "tiempo_alistar": float(pp["tiempo_alistar"]),
        "tiempo_finalizacion": float(pp["tiempo_finalizacion"]),
        "tiempo_gracia": float(pp["tiempo_gracia"]),
        "early_buffer": float(pp["early_buffer"]),
        "workday_end_dt": workday_end_dt,
        "duraciones_df": duraciones_df,
        "time_method": str(pp.get("time_method", "speed_based")),
        "time_dict": _df_to_time_dict(_read_parquet(tmpdir / files["time_dict"])) if "time_dict" in files else {},
    }


# ---------------------------------------------------------------------------
# Probe mode — serialize output
# ---------------------------------------------------------------------------

def serialize_probe_output(
    tmpdir: Path,
    result: Dict[str, Any],
) -> Path:
    """
    Write insertion-probe output to tmpdir.  Returns path to probe_output.json.

    Args:
        tmpdir  : Temp directory (must exist).
        result  : The dict returned by run_insertion_worker
                  (keys: valid, seed, num_inserted, dist, results, moves).
    """
    tmpdir = Path(tmpdir)

    num_inserted = result.get("num_inserted", 0)

    # Serialize result DataFrames if present
    results_df = result.get("results")
    moves_df = result.get("moves")

    has_results = isinstance(results_df, pd.DataFrame) and not results_df.empty
    has_moves = isinstance(moves_df, pd.DataFrame) and not moves_df.empty

    if has_results:
        _write_parquet(results_df, tmpdir / "probe_result.parquet")
    if has_moves:
        _write_parquet(moves_df, tmpdir / "probe_moves.parquet")

    # Scalar fields
    scalar_result = {
        k: v for k, v in result.items()
        if k not in ("results", "moves") and not isinstance(v, pd.DataFrame)
    }
    try:
        json.dumps(scalar_result, default=str)
    except (TypeError, ValueError):
        scalar_result = {"num_inserted": num_inserted}

    envelope = {
        "serde_version": SERDE_VERSION,
        "mode": "probe",
        "num_inserted": num_inserted,
        "result": scalar_result,
        "has_results": has_results,
        "has_moves": has_moves,
        "files": {
            **({"probe_result": "probe_result.parquet"} if has_results else {}),
            **({"probe_moves": "probe_moves.parquet"} if has_moves else {}),
        },
    }

    output_json = tmpdir / "probe_output.json"
    output_json.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    return output_json


# ---------------------------------------------------------------------------
# Probe mode — deserialize output
# ---------------------------------------------------------------------------

def deserialize_probe_output(
    output_json_path: Path,
) -> Dict[str, Any]:
    """
    Read probe outputs written by serialize_probe_output.

    Returns a dict with the same keys as run_insertion_worker's return value:
    valid, seed, num_inserted, dist, results, moves.
    """
    output_json_path = Path(output_json_path)
    envelope = json.loads(output_json_path.read_text(encoding="utf-8"))
    _check_version(envelope, "probe_output")
    tmpdir = output_json_path.parent
    files = envelope.get("files", {})

    result = dict(envelope.get("result", {}))
    result["num_inserted"] = envelope.get("num_inserted", 0)

    if envelope.get("has_results") and "probe_result" in files:
        result["results"] = _read_parquet(tmpdir / files["probe_result"])
    else:
        result["results"] = pd.DataFrame()

    if envelope.get("has_moves") and "probe_moves" in files:
        result["moves"] = _read_parquet(tmpdir / files["probe_moves"])
    else:
        result["moves"] = pd.DataFrame()

    return result
