## Conversation Memory + Recovery Checklist

This file is for resuming in a new chat after accidental undo/revert.

It records:
- what was discussed
- what should exist in code
- how to verify the repo is back to a functioning baseline
- the long/manual workflow before optimization/compression
- the current one-command orchestrated workflow

---

## 1) Core context from this conversation

- You are testing **LingBot-Depth** for a research pipeline:
  - UAV oblique RGB image
  - depth generation/refinement
  - traversability cost map
  - A* path planning for UGV
- You started with environment issues (CPU torch vs CUDA/xformers mismatch), then got GPU execution working.
- You tested with RGB-only images (coffee shop, then park), using **Depth Anything V2 Metric** to create synthetic depth input.
- You discovered:
  - LingBot mask can produce black holes outdoors (`--no-mask` helps for dense output)
  - BEV `cost_map.png` looks abstract/small and is hard to align mentally with RGB
  - path failure often occurs because chosen start/goal pixels map to blocked/disconnected BEV cells
- You requested a smoother workflow and asked to consolidate/automate.
- PowerShell note: when pasting multi-line commands, do **not** include the `>>` continuation prompt text; only use trailing backticks `` ` `` for line continuation.

---

## 2) Expected repository state (what should have been implemented)

### A. Baseline helper scripts and modules

These should exist at **repo root** and be functional:

- `prepare_rgb_only.py`
  - RGB -> `rgb.png`, `raw_depth.png` (16-bit mm), `intrinsics.txt`
  - uses Depth Anything V2 Metric through `transformers`
- `plan_ugv_path.py`
  - depth + intrinsics + extrinsics (`pitch`, `cam_height`) -> BEV elevation
  - slope -> cost classes `{0.0, 0.5, 1.0}`
  - A* path from CLI `--start u,v --goal u,v`
  - outputs `elevation.png`, `slope.png`, `cost_map.png`, `path_overlay.png`, CSVs
- `pick_nav_points.py`
  - creates `cost_on_rgb.png` overlay (traversability projected on camera image)
  - interactive click mode to pick start/goal in RGB frame
  - prints `START: u,v = ...` and `GOAL: u,v = ...` (parsed by the orchestrator)
  - supports a resizable display / practical click UX
  - `--no-interactive` writes overlay only
- `nav/elevation.py`, `nav/traversability.py`, `nav/astar.py`, `nav/__init__.py`
  - geometry, costing, planner internals

### B. Orchestrator (implemented)

- `run_uav_navigation.py` is the **single entrypoint** that chains:
  1) prepare RGB-only inputs (`prepare_rgb_only.py`)
  2) run LingBot refinement (`example.py`)
  3) pick start/goal interactively (`pick_nav_points.py`) **or** use CLI `--start/--goal`
  4) run path planning (`plan_ugv_path.py`) automatically

**Key behavior (updated):** after interactive START/GOAL selection, the orchestrator captures `pick_nav_points.py` stdout, parses the printed coordinates, and immediately runs A* planning — no second manual re-run required.

Priority for start/goal:
1. Explicit CLI `--start` and `--goal` if both provided
2. Else coordinates parsed from interactive click output
3. Else skip planning with a message

Useful flags:
- `--no-mask` for outdoor dense depth
- `--skip-click-ui` to only write `cost_on_rgb.png` (no interactive pick / no auto-plan unless CLI coords given)

### C. Dependencies

- `transformers>=4.45.0` should be present in project dependencies for Depth Anything integration.

---

## 3) Preferred one-command workflow (current)

```powershell
uv run python run_uav_navigation.py `
  --image "examples/sample_pictures/park.jpg" `
  --scene outdoor `
  --no-mask `
  --pitch-deg -35 --cam-height-m 15 --resolution 0.5 `
  --slope-safe 30 --slope-max 55 `
  --path-out result_path_park
```

Then:
1. Wait for prepare + LingBot refine + `cost_on_rgb.png`
2. Click START, then GOAL in the OpenCV window; press `q`
3. Path planning runs automatically with the selected `u,v` coordinates

Optional non-interactive override:

```powershell
uv run python run_uav_navigation.py `
  --image "examples/sample_pictures/park.jpg" `
  --scene outdoor --no-mask `
  --pitch-deg -35 --cam-height-m 15 --resolution 0.5 `
  --slope-safe 30 --slope-max 55 `
  --path-out result_path_park `
  --start 116,1245 --goal 855,916
```

---

## 4) Long manual workflow (pre-optimization, pre-compression)

This is the known-working manual pipeline before orchestration (still useful for debugging individual stages):

1. Prepare RGB-only example:

```powershell
uv run python prepare_rgb_only.py --image "path\to\image.jpg" --out examples/my_scene --scene outdoor
```

2. Run LingBot refinement:

```powershell
$env:PYTHONIOENCODING='utf-8'
uv run python example.py --example my_scene --output result_my_scene --no-mask
```

3. Generate traversability overlay and pick points:

```powershell
uv run python pick_nav_points.py `
  --depth result_my_scene/depth_refined.npy `
  --intrinsics examples/my_scene/intrinsics.txt `
  --rgb result_my_scene/rgb.png `
  --pitch-deg -35 --cam-height-m 15 --resolution 0.5 `
  --slope-safe 30 --slope-max 55 `
  --out result_path_park
```

4. Plan path with selected start/goal:

```powershell
uv run python plan_ugv_path.py `
  --depth result_my_scene/depth_refined.npy `
  --intrinsics examples/my_scene/intrinsics.txt `
  --rgb result_my_scene/rgb.png `
  --pitch-deg -35 --cam-height-m 15 --resolution 0.5 `
  --slope-safe 30 --slope-max 55 `
  --start <u1>,<v1> --goal <u2>,<v2> `
  --out result_path_park
```

---

## 5) Recovery verification checklist

## Implemented improvements (this conversation)

- RGB-only input support via `prepare_rgb_only.py` (Depth Anything V2 Metric + intrinsics approximation) → `examples/<name>/`.
- End-to-end refinement + traversability overlay:
  - LingBot-Depth via `example.py`
  - Traversability projection + start/goal picking with `pick_nav_points.py` (writes `cost_on_rgb.png`).
- Navigation stack:
  - BEV elevation in `nav/elevation.py`
  - Slope → cost in `nav/traversability.py`
  - 8-connected A* in `nav/astar.py`
- Correctness fix: `nav/elevation.py::pixel_to_bev_index()` now backprojects using **absolute** `(u, v)` pixel coordinates, so clicked yellow regions match the queried cost (no more “yellow but BLOCKED” mismatch).
- Orchestrator UX fix: `run_uav_navigation.py` now auto-runs `plan_ugv_path.py` after interactive START/GOAL selection by parsing printed coordinates from `pick_nav_points.py` stdout (no second full pipeline re-run).

## Subsequent steps

1. Verify one-command end-to-end on park (and a few more UAV frames):
   `run_uav_navigation.py` → click START/GOAL → automatic A* outputs in `--path-out`.
2. For outdoor RGB-only prototyping, keep using `--no-mask` unless you intentionally want to study the LingBot confidence mask.
3. Remove the click step next:
   add a non-interactive start/goal mode (auto-pick from the largest connected passable region).
4. Improve stability next:
   add BEV smoothing before slope so the cost map is less speckled.
5. Scale up next:
   batch-process multiple UAV frames and (optionally) temporally fuse BEV elevation/cost for smoother planning.

---

# Memory — UAV depth to UGV path pipeline

Last updated: Saturday Aug 15, 2026, 7:13 PM (UTC+8)

## What was built

- RGB-only prep: `prepare_rgb_only.py` (Depth Anything V2 Metric → 16-bit mm `raw_depth.png` + FOV/EXIF `intrinsics.txt`).
- LingBot refinement still via `example.py`.
- Navigation stack in `nav/`: `elevation.py`, `traversability.py`, `astar.py`.
- Human-readable overlay + click picker: `pick_nav_points.py` writes `cost_on_rgb.png` (green 0.0 / yellow 0.5 / red 1.0).
- Planner CLI: `plan_ugv_path.py` (BEV elevation → slope cost → A* from pixel `--start u,v --goal u,v`).
- Orchestrator: `run_uav_navigation.py` chains prepare → `example.py` → pick overlay → parse printed START/GOAL from stdout → auto-run A*.
- CUDA env: PyTorch `2.6.0+cu124` + matching xformers (CPU torch fails xformers attention).

## Decisions made

- Research setup is UAV **oblique forward-looking** RGB; path is for a **UGV dog**.
- Start/goal are **image pixels** `(u,v)`, not BEV cells.
- Cost is **slope on BEV**, not semantic path vs grass. Yellow walkway vs green lawn is expected.
- Outdoor RGB-only demos use `--no-mask` so LingBot does not punch black holes.
- Orchestrator calls **repo-root** scripts (`prepare_rgb_only.py`, `pick_nav_points.py`, `plan_ugv_path.py`), not `helper/`.
- Default park-tuning: `--pitch-deg -35 --cam-height-m 15 --resolution 0.5 --slope-safe 30 --slope-max 55`.

## Problems solved

- CPU PyTorch vs CUDA xformers mismatch; install cu124 wheels from PyTorch index.
- Windows `UnicodeEncodeError` on `example.py` emojis: `$env:PYTHONIOENCODING='utf-8'`.
- Mixed-resolution leftover `raw_depth.png` vs new RGB broke concat in `example.py`.
- `cost_map.png` is a tiny BEV footprint; pick points on `cost_on_rgb.png` instead.
- Click-vs-planner mismatch: `pixel_to_bev_index()` cropped depth to 1×1 so `cx/cy` were wrong. Now backprojects with absolute `(u,v)`. Confirmed: `(69,1222)` and `(888,894)` map to cost 0.5 and A* finds a path (44 cells).
- Stale `path_on_rgb.png` if planning fails (file not overwritten). Use a fresh `--out` folder.

## Current state

- End-to-end **works** on park (`1080×1350`): prepare → refine → click yellow/green → A*.
- `run_uav_navigation.py` exists and auto-plans after clicks when stdout is captured.
- Cost overlay is noisy/speckled; passable corridors are small. Guessed pitch/height/intrinsics; RGB-only depth is synthetic.
- Park image: `examples/sample_pictures/park.jpg` → typically `examples/my_scene/` + `result_my_scene/` + `result_path_park/`.

## Next session starts with

Verify one park run of `uv run python run_uav_navigation.py --image "examples/sample_pictures/park.jpg" --scene outdoor --no-mask --pitch-deg -35 --cam-height-m 15 --resolution 0.5 --slope-safe 30 --slope-max 55 --path-out result_path_park` (click START then GOAL; planning should run automatically). Then add non-interactive auto start/goal (largest connected passable region).

## Open questions

- Whether to add BEV elevation smoothing before slope (reduce speckle).
- Whether to batch UAV frames / temporally fuse BEV.
- Real UAV extrinsics (gimbal pitch, AGL) vs the guessed defaults.
- Keep calling root scripts vs moving implementations fully into `helper/`.
