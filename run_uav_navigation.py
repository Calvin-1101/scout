#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str]) -> None:
    print('>', ' '.join(cmd))
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)

def _run_capture_stream(cmd: list[str]) -> str:
    """
    Run a subprocess and stream its output to the console while also capturing it.
    This lets us parse the START/GOAL coordinates printed by pick_nav_points.py.
    """
    print('>', ' '.join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
    )
    if proc.stdout is None:
        raise RuntimeError('Failed to capture subprocess stdout')

    chunks: list[str] = []
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        chunks.append(line)

    rc = proc.wait()
    if rc != 0:
        raise SystemExit(rc)
    return ''.join(chunks)


def prepare_inputs(image: str, example_out: Path, scene: str, fov: float) -> None:
    _run([
        sys.executable,
        'prepare_rgb_only.py',
        '--image', image,
        '--out', str(example_out),
        '--scene', scene,
        '--fov', str(fov),
    ])


def run_refinement(example_name: str, result_out: str, no_mask: bool) -> None:
    cmd = [
        sys.executable,
        'example.py',
        '--example', example_name,
        '--output', result_out,
    ]
    if no_mask:
        cmd.append('--no-mask')
    _run(cmd)


def generate_cost_rgb_map(
    depth_path: str,
    intrinsics_path: str,
    rgb_path: str,
    pitch_deg: float,
    cam_height_m: float,
    resolution: float,
    slope_safe: float,
    slope_max: float,
    path_out: str,
    interactive: bool,
) -> tuple[str | None, str | None]:
    """Generate traversability overlay on RGB (cost_on_rgb.png)."""
    cmd = [
        sys.executable,
        'pick_nav_points.py',
        '--depth', depth_path,
        '--intrinsics', intrinsics_path,
        '--rgb', rgb_path,
        '--pitch-deg', str(pitch_deg),
        '--cam-height-m', str(cam_height_m),
        '--resolution', str(resolution),
        '--slope-safe', str(slope_safe),
        '--slope-max', str(slope_max),
        '--out', path_out,
    ]
    if not interactive:
        cmd.append('--no-interactive')
        _run(cmd)
        return None, None

    # interactive=True: parse printed START/GOAL coordinates after the user clicks.
    output = _run_capture_stream(cmd)

    starts = re.findall(r"START:\s*u,v\s*=\s*(\d+)\s*,\s*(\d+)", output)
    goals = re.findall(r"GOAL:\s*u,v\s*=\s*(\d+)\s*,\s*(\d+)", output)
    if not starts or not goals:
        return None, None

    s_u, s_v = starts[0]
    g_u, g_v = goals[0]
    return f"{s_u},{s_v}", f"{g_u},{g_v}"


def run_path_planning(
    depth_path: str,
    intrinsics_path: str,
    rgb_path: str,
    pitch_deg: float,
    cam_height_m: float,
    resolution: float,
    slope_safe: float,
    slope_max: float,
    start: str,
    goal: str,
    path_out: str,
) -> None:
    _run([
        sys.executable,
        'plan_ugv_path.py',
        '--depth', depth_path,
        '--intrinsics', intrinsics_path,
        '--rgb', rgb_path,
        '--pitch-deg', str(pitch_deg),
        '--cam-height-m', str(cam_height_m),
        '--resolution', str(resolution),
        '--slope-safe', str(slope_safe),
        '--slope-max', str(slope_max),
        '--start', start,
        '--goal', goal,
        '--out', path_out,
    ])


def main() -> int:
    parser = argparse.ArgumentParser(
        description='One-command pipeline: prepare -> refine -> cost_on_rgb -> optional A*'
    )
    parser.add_argument('--image', required=True, help='Input RGB image path')
    parser.add_argument('--example-out', default='examples/my_scene')
    parser.add_argument('--result-out', default='result_my_scene')
    parser.add_argument('--path-out', default='result_path_park')
    parser.add_argument('--scene', choices=['indoor', 'outdoor'], default='outdoor')
    parser.add_argument('--fov', type=float, default=65.0)
    parser.add_argument('--pitch-deg', type=float, default=-35.0)
    parser.add_argument('--cam-height-m', type=float, default=15.0)
    parser.add_argument('--resolution', type=float, default=0.5)
    parser.add_argument('--slope-safe', type=float, default=30.0)
    parser.add_argument('--slope-max', type=float, default=55.0)
    parser.add_argument('--start', type=str, default=None, help='u,v')
    parser.add_argument('--goal', type=str, default=None, help='u,v')
    parser.add_argument('--no-mask', action='store_true')
    parser.add_argument(
        '--skip-click-ui',
        action='store_true',
        help='Only generate cost_on_rgb.png and skip interactive clicks',
    )
    args = parser.parse_args()

    example_out = Path(args.example_out)
    example_name = example_out.name
    depth_path = f"{args.result_out}/depth_refined.npy"
    intrinsics_path = f"{args.example_out}/intrinsics.txt"
    rgb_path = f"{args.result_out}/rgb.png"

    prepare_inputs(args.image, example_out, args.scene, args.fov)
    run_refinement(example_name, args.result_out, args.no_mask)
    selected_start, selected_goal = generate_cost_rgb_map(
        depth_path=depth_path,
        intrinsics_path=intrinsics_path,
        rgb_path=rgb_path,
        pitch_deg=args.pitch_deg,
        cam_height_m=args.cam_height_m,
        resolution=args.resolution,
        slope_safe=args.slope_safe,
        slope_max=args.slope_max,
        path_out=args.path_out,
        interactive=not args.skip_click_ui,
    )

    if args.start and args.goal:
        start, goal = args.start, args.goal
    elif selected_start and selected_goal:
        start, goal = selected_start, selected_goal
    else:
        start, goal = None, None

    if start and goal:
        run_path_planning(
            depth_path=depth_path,
            intrinsics_path=intrinsics_path,
            rgb_path=rgb_path,
            pitch_deg=args.pitch_deg,
            cam_height_m=args.cam_height_m,
            resolution=args.resolution,
            slope_safe=args.slope_safe,
            slope_max=args.slope_max,
            start=start,
            goal=goal,
            path_out=args.path_out,
        )
    else:
        print('Skipping final path planning: no START/GOAL selected (or pass --start and --goal).')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
