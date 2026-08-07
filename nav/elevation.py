from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math

import numpy as np


@dataclass
class BevGrid:
    elevation: np.ndarray
    origin_x: float
    origin_y: float
    resolution: float

    def grid_to_world(self, row: int, col: int) -> tuple[float, float]:
        x = self.origin_x + (col + 0.5) * self.resolution
        y = self.origin_y + (row + 0.5) * self.resolution
        return x, y

    @property
    def shape(self) -> tuple[int, int]:
        return self.elevation.shape


def load_intrinsics_pixel(path: str | Path, width: int, height: int) -> np.ndarray:
    K = np.loadtxt(path, dtype=np.float64)
    if K.shape != (3, 3):
        raise ValueError(f"intrinsics must be 3x3, got {K.shape}")
    # Accept either pixel intrinsics or normalized intrinsics in [0, 1].
    if K[0, 0] < 10.0 and K[1, 1] < 10.0:
        K = K.copy()
        K[0, 0] *= width
        K[1, 1] *= height
        K[0, 2] *= width
        K[1, 2] *= height
    return K


def _backproject(depth: np.ndarray, K: np.ndarray, pitch_deg: float, cam_height_m: float) -> np.ndarray:
    h, w = depth.shape
    v, u = np.indices((h, w))
    z_c = depth.astype(np.float64)
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]

    x_c = (u - cx) / fx * z_c
    y_c = (v - cy) / fy * z_c

    pitch = math.radians(pitch_deg)
    c, s = math.cos(pitch), math.sin(pitch)

    # Inverse of the 2D rotation used in ground_to_pixel() in planner code.
    y_r = c * y_c - s * z_c
    z_r = s * y_c + c * z_c

    x_g = x_c
    y_g = z_r
    z_g = cam_height_m - y_r
    return np.stack((x_g, y_g, z_g), axis=-1)


def backproject_pixels(depth: np.ndarray, K: np.ndarray, pitch_deg: float, cam_height_m: float) -> np.ndarray:
    return _backproject(depth, K, pitch_deg, cam_height_m)


def build_bev_elevation(
    depth: np.ndarray,
    K: np.ndarray,
    pitch_deg: float,
    cam_height_m: float,
    resolution: float,
) -> BevGrid:
    pts = _backproject(depth, K, pitch_deg, cam_height_m)
    valid = np.isfinite(pts).all(axis=-1) & (depth > 0.05)
    if not np.any(valid):
        return BevGrid(np.full((1, 1), np.nan, dtype=np.float64), 0.0, 0.0, resolution)

    x = pts[..., 0][valid]
    y = pts[..., 1][valid]
    z = pts[..., 2][valid]

    x_min, x_max = float(np.min(x)), float(np.max(x))
    y_min, y_max = float(np.min(y)), float(np.max(y))
    cols = max(1, int(np.ceil((x_max - x_min) / resolution)) + 1)
    rows = max(1, int(np.ceil((y_max - y_min) / resolution)) + 1)

    elev_sum = np.zeros((rows, cols), dtype=np.float64)
    elev_cnt = np.zeros((rows, cols), dtype=np.int32)

    col_i = np.floor((x - x_min) / resolution).astype(np.int32)
    row_i = np.floor((y - y_min) / resolution).astype(np.int32)
    inb = (row_i >= 0) & (row_i < rows) & (col_i >= 0) & (col_i < cols)
    row_i = row_i[inb]
    col_i = col_i[inb]
    z = z[inb]

    np.add.at(elev_sum, (row_i, col_i), z)
    np.add.at(elev_cnt, (row_i, col_i), 1)

    elevation = np.full((rows, cols), np.nan, dtype=np.float64)
    filled = elev_cnt > 0
    elevation[filled] = elev_sum[filled] / elev_cnt[filled]
    return BevGrid(elevation, x_min, y_min, resolution)


def pixel_to_bev_index(
    u: int,
    v: int,
    depth: np.ndarray,
    K: np.ndarray,
    pitch_deg: float,
    cam_height_m: float,
    grid: BevGrid,
) -> tuple[int, int] | None:
    h, w = depth.shape
    if u < 0 or v < 0 or u >= w or v >= h:
        return None
    d = float(depth[v, u])
    if not np.isfinite(d) or d <= 0.05:
        return None
    # Backproject using the *absolute* pixel coordinates (u,v) in the original image.
    # Important: we must NOT crop the depth to 1x1 before backprojection, because cx/cy
    # are defined in the full image coordinate system.
    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx, cy = float(K[0, 2]), float(K[1, 2])

    # Camera-frame point (x right, y down, z forward) at pixel (u,v)
    x_c = (u - cx) / fx * d
    y_c = (v - cy) / fy * d
    z_c = d

    # Apply the same pitch rotation math as _backproject.
    pitch = math.radians(pitch_deg)
    c, s = math.cos(pitch), math.sin(pitch)
    y_r = c * y_c - s * z_c
    z_r = s * y_c + c * z_c

    # Ground-frame point (X right, Y forward, Z up)
    x_g = float(x_c)
    y_g = float(z_r)

    col = int(math.floor((x_g - grid.origin_x) / grid.resolution))
    row = int(math.floor((y_g - grid.origin_y) / grid.resolution))
    rows, cols = grid.shape
    if row < 0 or row >= rows or col < 0 or col >= cols:
        return None
    return row, col
