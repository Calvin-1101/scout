"""Auto-select start/goal image pixels from BEV traversability."""

from __future__ import annotations

from collections import deque
import math

import numpy as np

from .elevation import BevGrid, backproject_pixels

_NEIGHBORS = (
    (-1, 0),
    (1, 0),
    (0, -1),
    (0, 1),
    (-1, -1),
    (-1, 1),
    (1, -1),
    (1, 1),
)


def _reachable_passable(
    cost: np.ndarray, start: tuple[int, int]
) -> set[tuple[int, int]]:
    rows, cols = cost.shape
    sr, sc = start
    if not (0 <= sr < rows and 0 <= sc < cols):
        return set()
    if cost[sr, sc] >= 1.0:
        return set()
    seen = {start}
    queue: deque[tuple[int, int]] = deque([start])
    while queue:
        r, c = queue.popleft()
        for dr, dc in _NEIGHBORS:
            nr, nc = r + dr, c + dc
            if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                continue
            if cost[nr, nc] >= 1.0:
                continue
            nxt = (nr, nc)
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return seen


def select_start_goal(
    depth: np.ndarray,
    K: np.ndarray,
    pitch_deg: float,
    cam_height_m: float,
    grid: BevGrid,
    cost: np.ndarray,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Pick a near-field start and farthest reachable goal as image pixels (u, v).

    Start is the passable pixel closest to the bottom-left of the image (UAV
    near-field). Goal is the reachable BEV cell farthest from that start in
    ground meters, mapped back to a representative pixel.
    """
    h, w = depth.shape
    pts = backproject_pixels(depth, K, pitch_deg, cam_height_m)
    valid = np.isfinite(pts).all(axis=-1) & (depth > 0.05)
    rows, cols = grid.shape
    col_i = np.floor((pts[..., 0] - grid.origin_x) / grid.resolution).astype(np.int32)
    row_i = np.floor((pts[..., 1] - grid.origin_y) / grid.resolution).astype(np.int32)
    in_grid = (
        valid
        & (row_i >= 0)
        & (row_i < rows)
        & (col_i >= 0)
        & (col_i < cols)
    )
    if not np.any(in_grid):
        return None

    cost_px = np.ones((h, w), dtype=np.float64)
    cost_px[in_grid] = cost[row_i[in_grid], col_i[in_grid]]
    passable_px = in_grid & (cost_px < 1.0)
    if not np.any(passable_px):
        return None

    vv, uu = np.nonzero(passable_px)
    denom_w = max(w - 1, 1)
    denom_h = max(h - 1, 1)
    scores = uu.astype(np.float64) / denom_w + (
        1.0 - vv.astype(np.float64) / denom_h
    )
    best = int(np.argmin(scores))
    start_u = int(uu[best])
    start_v = int(vv[best])
    start_cell = (int(row_i[start_v, start_u]), int(col_i[start_v, start_u]))

    cell_to_uv: dict[tuple[int, int], tuple[int, int]] = {}
    for v, u in zip(vv.tolist(), uu.tolist()):
        cell = (int(row_i[v, u]), int(col_i[v, u]))
        cell_to_uv[cell] = (int(u), int(v))
    cell_to_uv[start_cell] = (start_u, start_v)

    reachable = _reachable_passable(cost, start_cell)
    candidates = [cell for cell in reachable if cell in cell_to_uv]
    if not candidates:
        return None

    sx, sy = grid.grid_to_world(*start_cell)
    best_cell = start_cell
    best_key = (-1.0, float("-inf"))
    for cell in candidates:
        x, y = grid.grid_to_world(*cell)
        dist = math.hypot(x - sx, y - sy)
        key = (dist, y)
        if key > best_key:
            best_key = key
            best_cell = cell

    goal_uv = cell_to_uv[best_cell]
    return (start_u, start_v), goal_uv
