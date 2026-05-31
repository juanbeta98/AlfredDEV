from __future__ import annotations

import math
import os
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ModelParams:
    """
    Shared model parameters used across all optimization algorithms.

    Units:
    - Speeds: km/h
    - Times: minutes
    """

    # Reproducibility
    seed: int = 10

    # Speeds
    # alfred_speed_kmh: float = 30.0            # driver to first point
    alfred_speed_kmh: float = 20.0            # driver to first point (fallback)
    vehicle_transport_speed_kmh: float = 32  # vehicle transport speed

    # Driver move walk-buffer + transit model
    # Walk buffers are applied at both ends of every driver move.
    # For total_dist <= 2*walk_buffer the whole trip is pure walking.
    # For longer trips: (osrm_time - 2*walk_time) * transit_slowdown + 2*walk_time
    driver_move_walk_buffer_km: float = 0.3   # walking distance at each end (km)
    driver_move_walk_speed_kmh: float = 5.0   # walking speed
    driver_move_transit_slowdown: float = 1.8  # transit is this many times slower than OSRM car time

    # Times (minutes)
    tiempo_previo_min: int = 0        # minutes before schedule_date
    # tiempo_previo_min: int = 30        # minutes before schedule_date
    tiempo_gracia_min: int = 0
    tiempo_alistar_min: int = 30
    tiempo_other_min: int = 30
    tiempo_finalizacion_min: int = 15
    workday_end_str: str = "19:00:00"
    osrm_url: Optional[str] = field(default_factory=lambda: os.environ.get("OSRM_URL"))

    def validate(self) -> None:
        if self.seed < 0:
            raise ValueError("seed must be >= 0")

        if self.alfred_speed_kmh <= 0:
            raise ValueError("alfred_speed_kmh must be > 0")

        if self.vehicle_transport_speed_kmh <= 0:
            raise ValueError("vehicle_transport_speed_kmh must be > 0")

        for name, value in {
            "tiempo_previo_min": self.tiempo_previo_min,
            "tiempo_gracia_min": self.tiempo_gracia_min,
            "tiempo_alistar_min": self.tiempo_alistar_min,
            "tiempo_other_min": self.tiempo_other_min,
            "tiempo_finalizacion_min": self.tiempo_finalizacion_min,
        }.items():
            if value < 0:
                raise ValueError(f"{name} must be >= 0")

        if self.driver_move_walk_buffer_km < 0:
            raise ValueError("driver_move_walk_buffer_km must be >= 0")
        if self.driver_move_walk_speed_kmh <= 0:
            raise ValueError("driver_move_walk_speed_kmh must be > 0")
        if self.driver_move_transit_slowdown <= 0:
            raise ValueError("driver_move_transit_slowdown must be > 0")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def driver_move_time_min(
        self,
        dist_km: float,
        osrm_time_min: float,
    ) -> float:
        """
        Compute driver move travel time using the walk-buffer + transit model.

        For short trips (total_dist <= 2 * walk_buffer): pure walking.
        For longer trips: strip the walking contribution from the OSRM car time,
        apply the transit slowdown factor, then add walking time at both ends back.

        Falls back to alfred_speed_kmh if dist_km or osrm_time_min is NaN/invalid.
        """
        if math.isnan(dist_km):
            return 0.0
        walk_buffer = self.driver_move_walk_buffer_km
        walk_speed = self.driver_move_walk_speed_kmh
        walk_t = walk_buffer / walk_speed * 60  # minutes per walking end
        if math.isnan(osrm_time_min) or osrm_time_min <= 0:
            # Fallback: constant-speed formula
            return dist_km / self.alfred_speed_kmh * 60
        if dist_km <= walk_buffer * 2:
            return dist_km / walk_speed * 60
        transit_raw = max(osrm_time_min - 2 * walk_t, 0.0)
        return transit_raw * self.driver_move_transit_slowdown + 2 * walk_t

    # Optional: convenience conversions (often useful)
    def kmh_to_m_per_min(self, kmh: float) -> float:
        # 1 km = 1000 m, 1 h = 60 min
        return (kmh * 1000.0) / 60.0

    @property
    def alfred_speed_m_per_min(self) -> float:
        return self.kmh_to_m_per_min(self.alfred_speed_kmh)

    @property
    def vehicle_transport_speed_m_per_min(self) -> float:
        return self.kmh_to_m_per_min(self.vehicle_transport_speed_kmh)
