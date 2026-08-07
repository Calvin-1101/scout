from __future__ import annotations

from heapq import heappop, heappush
import math

import numpy as np


def _heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _reconstruct(
    came_from: dict[tuple[int, int], tuple[int, int]],
    current: tuple[int, int],
) -> list[tuple[int, int]]:
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


def astar(
    cost: np.ndarray,
    start: tuple[int, int] | None,
    goal: tuple[int, int] | None,
) -> list[tuple[int, int]] | None:
    if start is None or goal is None:
        return None
    rows, cols = cost.shape
    sr, sc = start
    gr, gc = goal
    if not (0 <= sr < rows and 0 <= sc < cols and 0 <= gr < rows and 0 <= gc < cols):
        return None
    if cost[sr, sc] >= 1.0 or cost[gr, gc] >= 1.0:
        return None

    neighbors = [
        (-1, 0, 1.0),
        (1, 0, 1.0),
        (0, -1, 1.0),
        (0, 1, 1.0),
        (-1, -1, math.sqrt(2.0)),
        (-1, 1, math.sqrt(2.0)),
        (1, -1, math.sqrt(2.0)),
        (1, 1, math.sqrt(2.0)),
    ]

    open_heap: list[tuple[float, tuple[int, int]]] = []
    heappush(open_heap, (0.0, start))
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], float] = {start: 0.0}

    while open_heap:
        _, current = heappop(open_heap)
        if current == goal:
            return _reconstruct(came_from, current)

        cr, cc = current
        current_g = g_score[current]
        for dr, dc, step_dist in neighbors:
            nr, nc = cr + dr, cc + dc
            if nr < 0 or nr >= rows or nc < 0 or nc >= cols:
                continue
            if cost[nr, nc] >= 1.0:
                continue
            step = step_dist * (1.0 + float(cost[nr, nc]))
            tentative = current_g + step
            nkey = (nr, nc)
            if tentative < g_score.get(nkey, float("inf")):
                came_from[nkey] = current
                g_score[nkey] = tentative
                f = tentative + _heuristic(nkey, goal)
                heappush(open_heap, (f, nkey))
    return None


def path_to_world_waypoints(
    path: list[tuple[int, int]],
    origin_x: float,
    origin_y: float,
    resolution: float,
) -> list[tuple[float, float]]:
    return [
        (
            origin_x + (col + 0.5) * resolution,
            origin_y + (row + 0.5) * resolution,
        )
        for row, col in path
    ]
