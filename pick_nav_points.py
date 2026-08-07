#!/usr/bin/env python3
"""
Pick start/goal on the RGB image with traversability feedback.

The BEV cost_map.png is hard to relate to the photo. This script:
  1. Builds the same cost grid as plan_ugv_path.py
  2. Saves cost_on_rgb.png (green/yellow/red on the camera image)
  3. Opens a window: click start, then goal — prints whether each point is
     passable and the plan_ugv_path.py command if A* can connect them.

Use the SAME --pitch-deg, --cam-height-m, --resolution, and slope flags as
when you run plan_ugv_path.py.

Example:
    uv run python pick_nav_points.py \\
        --depth result_my_scene/depth_refined.npy \\
        --intrinsics examples/my_scene/intrinsics.txt \\
        --rgb result_my_scene/rgb.png \\
        --pitch-deg -35 --cam-height-m 15 --resolution 0.5 \\
        --slope-safe 30 --slope-max 55 \\
        --out result_path_park
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from nav.astar import astar
from nav.elevation import (
    build_bev_elevation,
    load_intrinsics_pixel,
    pixel_to_bev_index,
    backproject_pixels,
)
from nav.traversability import elevation_to_cost_map
from plan_ugv_path import load_depth


def build_cost_on_rgb(
    rgb_bgr: np.ndarray,
    depth: np.ndarray,
    K_pixel: np.ndarray,
    pitch_deg: float,
    cam_height_m: float,
    grid,
    cost: np.ndarray,
    alpha: float = 0.55,
) -> np.ndarray:
    """Paint traversability on the image (same frame as rgb)."""
    h, w = depth.shape
    pts_g = backproject_pixels(depth, K_pixel, pitch_deg, cam_height_m)
    valid = np.isfinite(pts_g).all(axis=-1) & (depth > 0.05)

    rows = grid.shape[0]
    cols = grid.shape[1]
    col_i = np.floor((pts_g[..., 0] - grid.origin_x) / grid.resolution).astype(np.int32)
    row_i = np.floor((pts_g[..., 1] - grid.origin_y) / grid.resolution).astype(np.int32)
    in_grid = (
        valid
        & (row_i >= 0)
        & (row_i < rows)
        & (col_i >= 0)
        & (col_i < cols)
    )

    cost_px = np.ones((h, w), dtype=np.float64)
    cost_px[in_grid] = cost[row_i[in_grid], col_i[in_grid]]

    tint = np.full_like(rgb_bgr, 40)
    tint[cost_px <= 0.0] = (0, 200, 0)
    tint[(cost_px > 0.0) & (cost_px < 1.0)] = (0, 200, 255)
    tint[(cost_px >= 1.0) & in_grid] = (0, 0, 200)

    out = rgb_bgr.copy()
    out[in_grid] = (
        (1 - alpha) * rgb_bgr[in_grid].astype(np.float32)
        + alpha * tint[in_grid].astype(np.float32)
    ).astype(np.uint8)
    return out


def label_cost(c: float | None) -> str:
    if c is None:
        return "no BEV cell"
    if c <= 0.0:
        return "PASSABLE (safe)"
    if c < 1.0:
        return "PASSABLE (moderate)"
    return "BLOCKED"


def main() -> int:
    parser = argparse.ArgumentParser(description="Pick start/goal with cost overlay on RGB")
    parser.add_argument("--depth", required=True)
    parser.add_argument("--intrinsics", required=True)
    parser.add_argument("--rgb", required=True)
    parser.add_argument("--pitch-deg", type=float, default=-35.0)
    parser.add_argument("--cam-height-m", type=float, default=15.0)
    parser.add_argument("--resolution", type=float, default=0.5)
    parser.add_argument("--slope-safe", type=float, default=30.0)
    parser.add_argument("--slope-max", type=float, default=55.0)
    parser.add_argument("--out", default="result_path_park")
    parser.add_argument(
        "--max-display-height",
        type=int,
        default=900,
        help="Fit interactive window to this height (pixels); clicks map to full image",
    )
    parser.add_argument("--no-interactive", action="store_true", help="Only write cost_on_rgb.png")
    args = parser.parse_args()

    depth = load_depth(Path(args.depth))
    h, w = depth.shape
    K = load_intrinsics_pixel(args.intrinsics, w, h)
    grid = build_bev_elevation(
        depth, K, args.pitch_deg, args.cam_height_m, args.resolution
    )
    cost, _ = elevation_to_cost_map(
        grid.elevation,
        grid.resolution,
        slope_safe_deg=args.slope_safe,
        slope_max_deg=args.slope_max,
    )

    rgb = cv2.imread(args.rgb)
    if rgb is None:
        print(f"Error: cannot read {args.rgb}")
        return 1
    if rgb.shape[:2] != (h, w):
        print(f"Warning: RGB {rgb.shape[:2]} != depth {(h, w)}; resizing RGB")
        rgb = cv2.resize(rgb, (w, h))

    overlay = build_cost_on_rgb(
        rgb, depth, K, args.pitch_deg, args.cam_height_m, grid, cost
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "cost_on_rgb.png"
    cv2.imwrite(str(out_path), overlay)
    print(f"Saved {out_path}")
    print("  Green = safe, yellow = moderate, red = blocked, dark = no depth data")
    print("  Pick start/goal on green or yellow regions in this image.")

    if args.no_interactive:
        return 0

    h, w = overlay.shape[:2]
    max_h = max(400, args.max_display_height)
    scale = min(1.0, max_h / h)
    disp_w = int(round(w * scale))
    disp_h = int(round(h * scale))

    def to_image_xy(x_disp: int, y_disp: int) -> tuple[int, int]:
        try:
            _, _, win_w, win_h = cv2.getWindowImageRect(window)
        except cv2.error:
            win_w, win_h = disp_w, disp_h
        if win_w <= 0 or win_h <= 0:
            win_w, win_h = disp_w, disp_h
        u = int(round(x_disp * w / win_w))
        v = int(round(y_disp * h / win_h))
        u = max(0, min(w - 1, u))
        v = max(0, min(h - 1, v))
        return u, v

    def display_frame() -> np.ndarray:
        try:
            _, _, win_w, win_h = cv2.getWindowImageRect(window)
        except cv2.error:
            win_w, win_h = disp_w, disp_h
        if win_w <= 0 or win_h <= 0:
            win_w, win_h = disp_w, disp_h
        return cv2.resize(overlay, (win_w, win_h), interpolation=cv2.INTER_AREA)

    picks: list[tuple[int, int]] = []
    window = "pick start then goal (q=quit, drag corner to resize)"

    def on_mouse(event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        u, v = to_image_xy(x, y)
        picks.append((u, v))
        d = float(depth[v, u]) if depth[v, u] > 0 else 0.0
        cell = pixel_to_bev_index(
            u, v, depth, K, args.pitch_deg, args.cam_height_m, grid
        )
        cval = float(cost[cell]) if cell else None
        role = "START" if len(picks) == 1 else "GOAL"
        print(f"{role}: u,v = {u},{v}  depth = {d:.2f} m  ->  {label_cost(cval)}")
        radius = max(4, int(8 / scale))
        cv2.circle(
            overlay,
            (u, v),
            radius,
            (0, 255, 0) if len(picks) == 1 else (0, 0, 255),
            -1,
            cv2.LINE_AA,
        )
        if len(picks) >= 2:
            s_cell = pixel_to_bev_index(
                picks[0][0], picks[0][1], depth, K, args.pitch_deg, args.cam_height_m, grid
            )
            g_cell = pixel_to_bev_index(
                picks[1][0], picks[1][1], depth, K, args.pitch_deg, args.cam_height_m, grid
            )
            path = astar(cost, s_cell, g_cell) if s_cell and g_cell else None
            if path:
                print(f"A* can connect ({len(path)} cells). Run:")
                print(
                    f"  uv run python plan_ugv_path.py --depth {args.depth} "
                    f"--intrinsics {args.intrinsics} --rgb {args.rgb} "
                    f"--pitch-deg {args.pitch_deg} --cam-height-m {args.cam_height_m} "
                    f"--resolution {args.resolution} --slope-safe {args.slope_safe} "
                    f"--slope-max {args.slope_max} "
                    f"--start {picks[0][0]},{picks[0][1]} --goal {picks[1][0]},{picks[1][1]} "
                    f"--out {args.out}"
                )
            else:
                print("A* cannot connect these points — try other pixels (green/yellow) or tune slopes.")

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, disp_w, disp_h)
    cv2.setMouseCallback(window, on_mouse)
    print(
        f"Display size {disp_w}x{disp_h} (image {w}x{h}, scale={scale:.3f}). "
        "Drag window edges to resize. Clicks map to full resolution."
    )
    print("Click START on green/yellow ground, then GOAL. Press q when done.")
    while True:
        cv2.imshow(window, display_frame())
        if cv2.waitKey(20) & 0xFF == ord("q"):
            break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
