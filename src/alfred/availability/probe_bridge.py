"""
probe_bridge.py — Drop-in replacement for run_insertion_worker in AlfredProd.

This module is **only used in AlfredProd** (never in AlfredDEV, which has the
real insert_algorithms module).  It exposes a function with the identical
signature as run_insertion_worker:

    result = run_insertion_worker_bridge(
        base_labors_df=..., base_moves_df=..., new_labors_df=...,
        seed=0, city=..., fecha=..., directorio_df=..., drivers=...,
        dist_dict=..., distance_method=..., alfred_speed=...,
        vehicle_transport_speed=..., tiempo_alistar=...,
        tiempo_finalizacion=..., tiempo_gracia=..., early_buffer=...,
        workday_end_dt=..., duraciones_df=..., time_method=..., time_dict=...,
    )

In feasibility_probe.py (AlfredProd), the import is swapped to:

    from alfred.availability.probe_bridge import run_insertion_worker_bridge as run_insertion_worker

So probe_slot() and all callers remain completely unchanged.

It also exposes run_batch_probe_bridge for Phase 2 slot scanning, which
serializes all slot candidates into a single binary invocation instead of
spawning one process per slot:

    results = run_batch_probe_bridge(
        candidates=[new_labors_df_slot0, new_labors_df_slot1, ...],
        base_labors_df=..., base_moves_df=..., city=..., fecha=...,
        directorio_df=..., drivers=..., dist_dict=..., distance_method=...,
        alfred_speed=..., vehicle_transport_speed=..., tiempo_alistar=...,
        tiempo_finalizacion=..., tiempo_gracia=..., early_buffer=...,
        workday_end_dt=..., duraciones_df=..., time_method=..., time_dict=...,
    )
    # returns List[Dict] — [{slot_index, num_inserted}, ...] one per candidate

Configuration (via environment variables):
    ALFRED_SOLVER_BIN           Path to the alfred_solver binary.
                                Default: bin/alfred_solver (relative to repo root).
    ALFRED_PROBE_TIMEOUT        Subprocess timeout in seconds for a single probe.
                                Default: 60.
    ALFRED_BATCH_PROBE_TIMEOUT  Subprocess timeout in seconds for a batch probe.
                                Default: 300 (5 min — covers a full day's worth of
                                slots without being as tight as a single probe).
    ALFRED_LICENSE              Path to the license file issued for this deployment.
                                Required — the binary will exit with code 5 if absent.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _default_binary_path() -> Path:
    """Resolve default binary path: <repo_root>/bin/alfred_solver."""
    # This file lives at src/alfred/availability/probe_bridge.py → repo root is 4 levels up.
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    suffix = ".exe" if os.name == "nt" else ""
    return repo_root / "bin" / f"alfred_solver{suffix}"


def _solver_bin() -> Path:
    env_path = os.environ.get("ALFRED_SOLVER_BIN")
    return Path(env_path) if env_path else _default_binary_path()


def _probe_timeout() -> float:
    return float(os.environ.get("ALFRED_PROBE_TIMEOUT", "60"))


def _batch_probe_timeout() -> float:
    return float(os.environ.get("ALFRED_BATCH_PROBE_TIMEOUT", "300"))


def _license_path() -> Optional[str]:
    return os.environ.get("ALFRED_LICENSE")


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class ProbeBridgeError(RuntimeError):
    """Raised when the alfred_solver binary fails during a probe."""


# ---------------------------------------------------------------------------
# Public function — same signature as run_insertion_worker
# ---------------------------------------------------------------------------

def run_insertion_worker_bridge(
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
    model_params=None,
) -> Dict[str, Any]:
    """
    Delegate a single insertion-feasibility probe to the alfred_solver binary.

    Identical signature to run_insertion_worker from insert_algorithms.py.
    Returns the same result dict: {valid, seed, num_inserted, dist, results, moves}.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="alfred_probe_"))
    try:
        return _run_probe(
            tmpdir=tmpdir,
            base_labors_df=base_labors_df,
            base_moves_df=base_moves_df,
            new_labors_df=new_labors_df,
            seed=seed,
            city=city,
            fecha=fecha,
            directorio_df=directorio_df,
            drivers=drivers,
            dist_dict=dist_dict,
            distance_method=distance_method,
            alfred_speed=alfred_speed,
            vehicle_transport_speed=vehicle_transport_speed,
            tiempo_alistar=tiempo_alistar,
            tiempo_finalizacion=tiempo_finalizacion,
            tiempo_gracia=tiempo_gracia,
            early_buffer=early_buffer,
            workday_end_dt=workday_end_dt,
            duraciones_df=duraciones_df,
            time_method=time_method,
            time_dict=time_dict,
        )
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception as exc:
            logger.warning("probe_bridge: failed to clean tmpdir %s: %s", tmpdir, exc)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _run_probe(tmpdir: Path, **kwargs) -> Dict[str, Any]:
    from alfred.optimization.solver_serde import serialize_probe_input, deserialize_probe_output

    solver_bin = _solver_bin()
    timeout = _probe_timeout()

    if not solver_bin.exists():
        raise ProbeBridgeError(
            f"alfred_solver binary not found at {solver_bin}. "
            "Set ALFRED_SOLVER_BIN or install the binary to bin/alfred_solver."
        )

    license = _license_path()

    input_json = serialize_probe_input(tmpdir, **kwargs)
    log_file = tmpdir / "probe.log"

    cmd = [
        str(solver_bin),
        "--mode", "probe",
        str(input_json),
        str(tmpdir),
        "--log-file", str(log_file),
        "--log-level", "WARNING",   # probes are noisy; only log warnings+
    ]
    if license:
        cmd += ["--license", license]

    logger.debug(
        "probe_bridge: launching probe city=%s fecha=%s seed=%s",
        kwargs.get("city"),
        kwargs.get("fecha"),
        kwargs.get("seed"),
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ProbeBridgeError(
            f"alfred_solver probe timed out after {timeout}s "
            f"(city={kwargs.get('city')}, fecha={kwargs.get('fecha')})"
        )

    # Forward stderr lines at warning level (capped to avoid log flooding)
    if proc.stderr.strip():
        lines = proc.stderr.strip().splitlines()
        for line in lines[:20]:
            logger.warning("probe_stderr | %s", line)
        if len(lines) > 20:
            logger.warning("probe_stderr | ... (%d more lines suppressed)", len(lines) - 20)

    if proc.returncode == 5:
        raise ProbeBridgeError(
            f"alfred_solver rejected the license file at '{license}'. "
            "Check that the file exists, is not expired, and was issued for this deployment."
        )
    if proc.returncode != 0:
        raise ProbeBridgeError(
            f"alfred_solver probe exited with code {proc.returncode} "
            f"(city={kwargs.get('city')}, fecha={kwargs.get('fecha')})"
        )

    output_json_str = proc.stdout.strip()
    if not output_json_str:
        raise ProbeBridgeError("alfred_solver probe produced no output path on stdout")

    output_json = Path(output_json_str)
    if not output_json.exists():
        raise ProbeBridgeError(
            f"alfred_solver probe reported output path {output_json} but it does not exist"
        )

    result = deserialize_probe_output(output_json)

    logger.debug(
        "probe_bridge: done num_inserted=%d city=%s",
        result.get("num_inserted", 0),
        kwargs.get("city"),
    )
    return result


# ---------------------------------------------------------------------------
# Batch probe — one binary launch for all Phase 2 slot candidates
# ---------------------------------------------------------------------------

def run_batch_probe_bridge(
    candidates: List[pd.DataFrame],
    *,
    base_labors_df: pd.DataFrame,
    base_moves_df: pd.DataFrame,
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
) -> List[Dict[str, Any]]:
    """
    Run feasibility probes for all slot candidates in a single binary launch.

    Serializes all candidate new_labors_df DataFrames alongside the shared base
    schedule into one batch_probe_input.json, invokes alfred_solver once with
    --mode batch_probe, and returns a list of {slot_index, num_inserted} dicts
    in the same order as the input candidates list.

    Args:
        candidates      : List of new_labors_df DataFrames, one per slot to probe.
        All other kwargs: Shared base schedule and model parameters — identical
                          to the run_insertion_worker_bridge signature minus
                          new_labors_df and seed (seed is always 0 per slot).

    Returns:
        List[Dict] with keys {slot_index, num_inserted}, one entry per candidate.

    Raises:
        ProbeBridgeError: If the binary fails, times out, or produces no output.
    """
    if not candidates:
        return []

    tmpdir = Path(tempfile.mkdtemp(prefix="alfred_batch_probe_"))
    try:
        return _run_batch_probe(
            tmpdir=tmpdir,
            candidates=candidates,
            base_labors_df=base_labors_df,
            base_moves_df=base_moves_df,
            city=city,
            fecha=fecha,
            directorio_df=directorio_df,
            drivers=drivers,
            dist_dict=dist_dict,
            distance_method=distance_method,
            alfred_speed=alfred_speed,
            vehicle_transport_speed=vehicle_transport_speed,
            tiempo_alistar=tiempo_alistar,
            tiempo_finalizacion=tiempo_finalizacion,
            tiempo_gracia=tiempo_gracia,
            early_buffer=early_buffer,
            workday_end_dt=workday_end_dt,
            duraciones_df=duraciones_df,
            time_method=time_method,
            time_dict=time_dict,
        )
    finally:
        try:
            shutil.rmtree(tmpdir)
        except Exception as exc:
            logger.warning("probe_bridge: failed to clean batch tmpdir %s: %s", tmpdir, exc)


def _run_batch_probe(tmpdir: Path, *, candidates: List[pd.DataFrame], **kwargs) -> List[Dict[str, Any]]:
    from alfred.optimization.solver_serde import (
        serialize_batch_probe_input,
        deserialize_batch_probe_output,
    )

    solver_bin = _solver_bin()
    timeout = _batch_probe_timeout()

    if not solver_bin.exists():
        raise ProbeBridgeError(
            f"alfred_solver binary not found at {solver_bin}. "
            "Set ALFRED_SOLVER_BIN or install the binary to bin/alfred_solver."
        )

    license = _license_path()

    input_json = serialize_batch_probe_input(tmpdir, candidates=candidates, **kwargs)
    log_file = tmpdir / "batch_probe.log"

    cmd = [
        str(solver_bin),
        "--mode", "batch_probe",
        str(input_json),
        str(tmpdir),
        "--log-file", str(log_file),
        "--log-level", "WARNING",
    ]
    if license:
        cmd += ["--license", license]

    logger.debug(
        "probe_bridge: launching batch_probe city=%s fecha=%s candidates=%d",
        kwargs.get("city"),
        kwargs.get("fecha"),
        len(candidates),
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ProbeBridgeError(
            f"alfred_solver batch_probe timed out after {timeout}s "
            f"(city={kwargs.get('city')}, fecha={kwargs.get('fecha')}, candidates={len(candidates)})"
        )

    if proc.stderr.strip():
        lines = proc.stderr.strip().splitlines()
        for line in lines[:20]:
            logger.warning("batch_probe_stderr | %s", line)
        if len(lines) > 20:
            logger.warning("batch_probe_stderr | ... (%d more lines suppressed)", len(lines) - 20)

    if proc.returncode == 5:
        raise ProbeBridgeError(
            f"alfred_solver rejected the license file at '{license}'. "
            "Check that the file exists, is not expired, and was issued for this deployment."
        )
    if proc.returncode != 0:
        raise ProbeBridgeError(
            f"alfred_solver batch_probe exited with code {proc.returncode} "
            f"(city={kwargs.get('city')}, fecha={kwargs.get('fecha')})"
        )

    output_json_str = proc.stdout.strip()
    if not output_json_str:
        raise ProbeBridgeError("alfred_solver batch_probe produced no output path on stdout")

    output_json = Path(output_json_str)
    if not output_json.exists():
        raise ProbeBridgeError(
            f"alfred_solver batch_probe reported output path {output_json} but it does not exist"
        )

    results = deserialize_batch_probe_output(output_json)

    logger.debug(
        "probe_bridge: batch_probe done candidates=%d feasible=%d city=%s",
        len(candidates),
        sum(1 for r in results if r.get("num_inserted", 0) > 0),
        kwargs.get("city"),
    )
    return results


# ---------------------------------------------------------------------------
# Driver helpers — available in AlfredProd (insert_algorithms.py is stripped)
# ---------------------------------------------------------------------------

def _filter_drivers_by_city(directorio_df: pd.DataFrame, city: Any) -> pd.DataFrame:
    """Return the subset of directorio_df whose department matches *city*."""
    if directorio_df is None or directorio_df.empty:
        return pd.DataFrame()

    department_key = str(city).strip()
    if not department_key:
        return directorio_df.iloc[0:0].copy()

    candidate_masks: List[pd.Series] = []
    for col in ("department_code", "department_name", "city"):
        if col in directorio_df.columns:
            candidate_masks.append(
                directorio_df[col].astype(str).str.strip() == department_key
            )

    for mask in candidate_masks:
        df_city = directorio_df.loc[mask].copy()
        if not df_city.empty:
            return df_city

    return directorio_df.iloc[0:0].copy()


def _extract_driver_key(driver_row: pd.Series) -> Optional[str]:
    """Return the canonical driver ID string from a directory row, or None."""
    for col in ("driver_id", "ALFRED'S"):
        if col in driver_row.index:
            value = driver_row.get(col)
            if pd.notna(value):
                key = str(value).strip()
                if key:
                    return key
    return None


def get_drivers(
    labors_algo_df: pd.DataFrame,
    directorio_df: pd.DataFrame,
    city: str,
    fecha=None,
    get_all: bool = True,
) -> List[str]:
    """Return driver IDs for a city. Uses directory if get_all=True, otherwise labors."""
    if get_all:
        dir_city = _filter_drivers_by_city(directorio_df, city)
        drivers: List[str] = []
        for _, row in dir_city.iterrows():
            key = _extract_driver_key(row)
            if key:
                drivers.append(key)
        return list(dict.fromkeys(drivers))  # deduplicate, preserve order
    else:
        mask = labors_algo_df["department_code"] == city
        if fecha is not None:
            fecha_date = pd.to_datetime(fecha).date()
            mask = mask & (labors_algo_df["schedule_date"].dt.date == fecha_date)
        drivers_series = labors_algo_df[mask]["assigned_driver"].dropna().astype(str)
        return [d for d in drivers_series.unique() if d.strip() not in ("", "nan", "None")]


def init_drivers(
    labors_df: pd.DataFrame,
    directorio_df: pd.DataFrame,
    city: str = "BOGOTA",
    ignore_schedule: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Initialise driver positions and availability for preassigned-state reconstruction."""
    import math as _math
    from datetime import datetime as _datetime, time as _time

    if labors_df.empty:
        return {}

    tz = labors_df["schedule_date"].dt.tz
    if tz is None:
        raise ValueError("La columna 'schedule_date' no tiene zona horaria asignada.")

    first_date = labors_df["schedule_date"].dt.date.min()
    if pd.isna(first_date):
        raise ValueError("No se pudo determinar la fecha mínima en 'schedule_date'.")

    df_ciudad = _filter_drivers_by_city(directorio_df, city)

    conductores: Dict[str, Any] = {}
    for _, conductor in df_ciudad.iterrows():
        if pd.isna(conductor["latitud"]) or pd.isna(conductor["longitud"]):
            continue

        driver_key = _extract_driver_key(conductor)
        if driver_key is None:
            continue

        if ignore_schedule:
            hora_inicio = _time(0, 0, 0)
        else:
            try:
                hora_inicio = _datetime.strptime(conductor["start_time"], "%H:%M:%S").time()
            except ValueError:
                raise ValueError(f"Formato invalido de hora para el conductor {driver_key}")

        disponibilidad = _datetime.combine(first_date, hora_inicio)

        conductores[driver_key] = {
            "position": f"POINT ({conductor['longitud']} {conductor['latitud']})",
            "available": pd.Timestamp(disponibilidad).tz_localize(tz),
            "work_start": hora_inicio,
        }

    return conductores


def assign_task_to_driver(
    driver_data: Dict[str, Any],
    arrival: pd.Timestamp,
    early: pd.Timestamp,
    start_point: str,
    end_point: str,
    is_last_in_service: bool,
    tiempo_alistar: int,
    tiempo_finalizacion: int,
    vehicle_speed: float,
    method: str,
    dist_dict: Optional[Dict[Any, Any]] = None,
    time_method: str = "speed_based",
    time_dict: Optional[Dict[Any, Any]] = None,
    **kwargs: Any,
) -> tuple:
    """Assign a task to a driver and update their availability and position."""
    import math as _math
    from datetime import timedelta as _timedelta

    from alfred.optimization.common.distance_utils import travel_time_minutes

    astart = max(arrival, early)
    dist_km, t_min, _, _ = travel_time_minutes(
        start_point, end_point,
        speed_kmh=vehicle_speed,
        time_method=time_method,
        dist_method=method,
        dist_dict=dist_dict,
        time_dict=time_dict,
        **kwargs,
    )
    dur = tiempo_alistar + (0.0 if _math.isnan(t_min) else t_min) \
          + (tiempo_finalizacion if not is_last_in_service else 0)
    aend = astart + _timedelta(minutes=dur)

    driver_data["available"] = aend
    driver_data["position"] = end_point

    return astart, aend, dist_km
