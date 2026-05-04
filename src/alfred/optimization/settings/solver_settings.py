from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .model_params import ModelParams
from .master_data import MasterDataParams

DEFAULT_DISTANCE_METHOD = "osrm"


@dataclass(frozen=True)
class OptimizationSettings:
    algorithm: str = "OFFLINE"
    distance_method: str = DEFAULT_DISTANCE_METHOD
    time_method: str = "osrm_times"   # "speed_based" | "osrm_times"
    # time_method: str = "speed_based"   # "speed_based" | "osrm_times"

    # shared hyperparameters
    alpha: float = 0.3
    # max_iterations per algorithm, then per department code.
    # Each algorithm receives its own flat dict[str, int] via for_algorithm().
    max_iterations: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "OFFLINE": {
                "25": 5000,  # Cundinamarca (Bogotá)
                "5":  2000,  # Antioquia (Medellín)
                "76": 2000,  # Valle del Cauca (Cali)
                "8":  500,   # Atlántico (Barranquilla)
                "13": 500,   # Bolívar (Cartagena)
                "68": 500,   # Santander (Bucaramanga)
                "66": 500,   # Risaralda (Pereira)
            },
            "INSERT": {
                "25": 750,
                "5":  400,
                "76": 400,
                "8":  200,
                "13": 200,
                "68": 200,
                "66": 200,
            },
            "REACT": {
                "25": 1500,
                "5":  1000,
                "76": 1000,
                "8":  500,
                "13": 500,
                "68": 500,
                "66": 500,
            },
        }
    )

    # execution controls
    n_processes: Optional[int] = None
    # pre-compute the full driver→labor distance matrix via OSRM Table API before iterations
    precompute_distances: bool = True

    # shared model parameters (timings, speeds, etc.)
    model_params: ModelParams = field(default_factory=ModelParams)
    # shared master data locations (csv/parquet)
    master_data: MasterDataParams = field(default_factory=MasterDataParams)

    # per-algorithm overrides (optional)
    overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def for_algorithm(self, name: str) -> Dict[str, Any]:
        """
        Returns settings for algorithm `name`, applying overrides if present.
        """
        base = {
            "alpha": self.alpha,
            "distance_method": self.distance_method,
            "time_method": self.time_method,
            "max_iterations": self.max_iterations.get(name.upper(), {}),
            "n_processes": self.n_processes,
            "precompute_distances": self.precompute_distances,
            "model_params": self.model_params,
            "master_data": self.master_data,
        }
        algo_over = self.overrides.get(name.upper(), {})
        return {**base, **algo_over}
