from __future__ import annotations

import numpy as np


def elevation_to_cost_map(
    elevation: np.ndarray,
    resolution: float,
    slope_safe_deg: float = 15.0,
    slope_max_deg: float = 30.0,
) -> tuple[np.ndarray, np.ndarray]:
    elev = elevation.astype(np.float64)
    valid = np.isfinite(elev)
    filled = np.where(valid, elev, np.nanmedian(elev[valid]) if np.any(valid) else 0.0)

    gy, gx = np.gradient(filled, resolution, resolution)
    slope_rad = np.arctan(np.sqrt(gx * gx + gy * gy))
    slope_deg = np.degrees(slope_rad)
    slope_deg[~valid] = np.nan

    cost = np.ones(elev.shape, dtype=np.float64)
    safe = valid & (slope_deg <= slope_safe_deg)
    moderate = valid & (slope_deg > slope_safe_deg) & (slope_deg <= slope_max_deg)
    cost[safe] = 0.0
    cost[moderate] = 0.5
    return cost, slope_deg
