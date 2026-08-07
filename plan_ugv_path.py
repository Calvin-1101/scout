#!/usr/bin/env python3
"""
Plan a UGV path from LingBot metric depth (oblique UAV imagery).

Pipeline: depth → BEV elevation → slope/cost → A* from CLI start/goal pixels.

Usage:
    uv run python plan_ugv_path.py \\
        --depth result_my_scene/depth_refined.npy \\
        --intrinsics examples/my_scene/intrinsics.txt \\
        --rgb result_my_scene/rgb.png \\
        --pitch-deg -35 --cam-height-m 30 --resolution 0.25 \\
        --start 100,600 --goal 900,200 \\
        --out result_path
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import cv2
import numpy as np

from nav.astar import astar, path_to_world_waypoints
from nav.elevation import (
    BevGrid,
    build_bev_elevation,
    load_intrinsics_pixel,
    pixel_to_bev_index,
)
from nav.traversability import elevation_to_cost_map


def parse_xy(s: str) -> tuple[int, int]:
    parts = s.replace(" ", "").split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Expected u,v got: {s}")
    return int(parts[0]), int(parts[1])


def load_depth(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".npy":
        depth = np.load(path).astype(np.float64)
    else:
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise FileNotFoundError(path)
        depth = raw.astype(np.float64) / 1000.0
    depth = np.nan_to_num(depth, nan=0.0, posinf=0.0, neginf=0.0)
    return depth


def save_float_map(arr: np.ndarray, path: Path, colormap=cv2.COLORMAP_TURBO) -> None:
    valid = np.isfinite(arr)
    if not valid.any():
        cv2.imwrite(str(path), np.zeros(arr.shape, dtype=np.uint8))
        return
    vmin, vmax = float(arr[valid].min()), float(arr[valid].max())
    norm = np.zeros(arr.shape, dtype=np.uint8)
    scaled = (arr - vmin) / (vmax - vmin + 1e-8)
    norm[valid] = np.clip(scaled[valid] * 255, 0, 255).astype(np.uint8)
    colored = cv2.applyColorMap(norm, colormap)
    colored[~valid] = (0, 0, 0)
    cv2.imwrite(str(path), colored)


def save_cost_map(cost: np.ndarray, path: Path) -> None:
    vis = np.zeros((*cost.shape, 3), dtype=np.uint8)
    vis[cost <= 0.0] = (0, 200, 0)
    vis[(cost > 0.0) & (cost < 1.0)] = (0, 200, 255)
    vis[cost >= 1.0] = (0, 0, 200)
    cv2.imwrite(str(path), vis)


def draw_path_on_grid(
    base: np.ndarray,
    path: list[tuple[int, int]],
    start: tuple[int, int],
    goal: tuple[int, int],
) -> np.ndarray:
    out = base.copy()
    if len(path) >= 2:
        pts = np.array([[c, r] for r, c in path], dtype=np.int32)
        cv2.polylines(out, [pts], False, (255, 255, 255), 2, cv2.LINE_AA)
    sr, sc = start
    gr, gc = goal
    cv2.circle(out, (sc, sr), 4, (0, 255, 0), -1, cv2.LINE_AA)
    cv2.circle(out, (gc, gr), 4, (0, 0, 255), -1, cv2.LINE_AA)
    return out


def ground_to_pixel(
    x: float,
    y: float,
    z: float,
    K_pixel: np.ndarray,
    pitch_deg: float,
    cam_height_m: float,
) -> tuple[int, int] | None:
    """Project ground-frame point to image pixel (u, v)."""
    pitch = math.radians(pitch_deg)
    c, s = math.cos(pitch), math.sin(pitch)
    y_r = cam_height_m - z
    z_r = y
    y_c = c * y_r + s * z_r
    z_c = -s * y_r + c * z_r
    x_c = x
    if z_c <= 0.05:
        return None
    fx, fy = K_pixel[0, 0], K_pixel[1, 1]
    cx, cy = K_pixel[0, 2], K_pixel[1, 2]
    u = int(round(fx * x_c / z_c + cx))
    v = int(round(fy * y_c / z_c + cy))
    return u, v


def path_to_image_pixels(
    path: list[tuple[int, int]],
    grid: BevGrid,
    K_pixel: np.ndarray,
    pitch_deg: float,
    cam_height_m: float,
    width: int,
    height: int,
) -> list[tuple[int, int]]:
    pixels = []
    for row, col in path:
        x, y = grid.grid_to_world(row, col)
        z = grid.elevation[row, col]
        if not np.isfinite(z):
            continue
        uv = ground_to_pixel(x, y, float(z), K_pixel, pitch_deg, cam_height_m)
        if uv is None:
            continue
        u, v = uv
        if 0 <= u < width and 0 <= v < height:
            pixels.append((u, v))
    return pixels


def main() -> int:
    parser = argparse.ArgumentParser(description="UAV depth → UGV traversability path")
    parser.add_argument("--depth", required=True, help=".npy (meters) or 16-bit depth PNG (mm)")
    parser.add_argument("--intrinsics", required=True, help="3x3 intrinsics.txt (pixels)")
    parser.add_argument("--rgb", default=None, help="Optional RGB for overlay")
    parser.add_argument("--pitch-deg", type=float, default=-35.0, help="Camera pitch (neg=down)")
    parser.add_argument("--cam-height-m", type=float, default=30.0, help="Camera height AGL (m)")
    parser.add_argument("--resolution", type=float, default=0.25, help="BEV cell size (m)")
    parser.add_argument("--slope-safe", type=float, default=15.0, help="Max safe slope (deg)")
    parser.add_argument("--slope-max", type=float, default=30.0, help="Max traversable slope (deg)")
    parser.add_argument("--start", type=parse_xy, required=True, help="Start pixel u,v")
    parser.add_argument("--goal", type=parse_xy, required=True, help="Goal pixel u,v")
    parser.add_argument("--out", default="result_path", help="Output directory")
    args = parser.parse_args()

    depth_path = Path(args.depth)
    depth = load_depth(depth_path)
    h, w = depth.shape

    K = load_intrinsics_pixel(args.intrinsics, w, h)
    grid = build_bev_elevation(
        depth, K, args.pitch_deg, args.cam_height_m, args.resolution
    )
    cost, slope = elevation_to_cost_map(
        grid.elevation,
        grid.resolution,
        slope_safe_deg=args.slope_safe,
        slope_max_deg=args.slope_max,
    )

    su, sv = args.start
    gu, gv = args.goal
    start_cell = pixel_to_bev_index(
        su, sv, depth, K, args.pitch_deg, args.cam_height_m, grid
    )
    goal_cell = pixel_to_bev_index(
        gu, gv, depth, K, args.pitch_deg, args.cam_height_m, grid
    )
    if start_cell is None or goal_cell is None:
        print("Error: could not map start or goal pixel to a valid BEV cell.")
        return 1

    path = astar(cost, start_cell, goal_cell)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    save_float_map(grid.elevation, out_dir / "elevation.png")
    save_float_map(slope, out_dir / "slope.png", cv2.COLORMAP_HOT)
    save_cost_map(cost, out_dir / "cost_map.png")

    cost_bgr = cv2.imread(str(out_dir / "cost_map.png"))
    if path:
        overlay = draw_path_on_grid(cost_bgr, path, start_cell, goal_cell)
        cv2.imwrite(str(out_dir / "path_overlay.png"), overlay)

        waypoints = path_to_world_waypoints(
            path, grid.origin_x, grid.origin_y, grid.resolution
        )
        with open(out_dir / "waypoints_xy.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["x_m", "y_m"])
            writer.writerows(waypoints)

        img_pixels = path_to_image_pixels(
            path, grid, K, args.pitch_deg, args.cam_height_m, w, h
        )
        with open(out_dir / "path_pixels.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["u", "v"])
            writer.writerows(img_pixels)

        if args.rgb and Path(args.rgb).exists():
            rgb = cv2.imread(str(args.rgb))
            if rgb is not None:
                if len(img_pixels) >= 2:
                    pts = np.array(img_pixels, dtype=np.int32)
                    cv2.polylines(rgb, [pts], False, (0, 255, 255), 2, cv2.LINE_AA)
                cv2.circle(rgb, (su, sv), 6, (0, 255, 0), -1, cv2.LINE_AA)
                cv2.circle(rgb, (gu, gv), 6, (0, 0, 255), -1, cv2.LINE_AA)
                cv2.imwrite(str(out_dir / "path_on_rgb.png"), rgb)

        print(f"Path found: {len(path)} BEV cells, {len(waypoints)} waypoints")
        print(f"Saved outputs to {out_dir}/")
    else:
        cv2.imwrite(str(out_dir / "path_overlay.png"), cost_bgr)
        print("No path found between start and goal on the cost map.")
        print(f"Partial outputs saved to {out_dir}/")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
