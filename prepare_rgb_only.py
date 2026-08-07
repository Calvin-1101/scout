#!/usr/bin/env python3
"""
Prepare a LingBot-Depth example folder from an RGB-only image.

LingBot-Depth refines sensor depth; it does not invent depth from RGB.
This script builds approximate inputs so you can run a qualitative demo:

  examples/<name>/
    rgb.png          # copied/converted from your photo
    raw_depth.png    # 16-bit PNG, millimeters (example.py divides by 1000)
    intrinsics.txt   # 3x3 camera matrix K in pixels:
                       fx  0  cx
                       0  fy  cy
                       0   0   1

Intrinsics are approximated from image size + horizontal FOV (or EXIF focal
length when available). Depth is estimated with Depth Anything V2 Metric
(transformers). Results are for qualitative testing only — use a real RGB-D
sensor for fair evaluation.

Usage:
    uv run python prepare_rgb_only.py --image path/to/photo.jpg
    uv run python prepare_rgb_only.py --image photo.jpg --fov 65 --out examples/my_scene
    uv run python example.py --example my_scene --output result_my_scene
"""

from __future__ import annotations

import argparse
import math
import shutil
from pathlib import Path

import cv2
import numpy as np
import torch


DEFAULT_MODEL = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
OUTDOOR_MODEL = "depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf"


def approximate_intrinsics(width: int, height: int, fov_deg: float) -> np.ndarray:
    """Build a pinhole K from image size and horizontal FOV (degrees)."""
    fx = (width / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    fy = fx
    cx = width / 2.0
    cy = height / 2.0
    return np.array(
        [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def intrinsics_from_exif(image_path: Path, width: int, height: int) -> np.ndarray | None:
    """Try EXIF FocalLengthIn35mmFilm → approximate fx; return None if missing."""
    try:
        from PIL import Image, ExifTags
    except ImportError:
        return None

    with Image.open(image_path) as img:
        exif = img.getexif()
        if not exif:
            return None
        tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        focal_35 = tag_map.get("FocalLengthIn35mmFilm")
        if not focal_35:
            return None
        # 35mm full-frame width ≈ 36mm; fx_px ≈ f35/36 * image_width
        fx = (float(focal_35) / 36.0) * width
        fy = fx
        cx = width / 2.0
        cy = height / 2.0
        return np.array(
            [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )


def write_intrinsics(path: Path, K: np.ndarray) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in K:
            f.write(f"{row[0]:.6f} {row[1]:.6f} {row[2]:.6f}\n")


def save_rgb(image_bgr: np.ndarray, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), image_bgr)


def depth_meters_to_uint16_mm(depth_m: np.ndarray) -> np.ndarray:
    depth_m = np.nan_to_num(depth_m, nan=0.0, posinf=0.0, neginf=0.0)
    depth_m = np.where(depth_m > 0, depth_m, 0.0)
    return (depth_m * 1000.0).clip(0, 65535).astype(np.uint16)


def sparsify_depth(depth_mm: np.ndarray, keep_ratio: float, seed: int = 0) -> np.ndarray:
    """Zero out random pixels to mimic sparse sensor depth for the DC model."""
    if keep_ratio >= 1.0:
        return depth_mm
    rng = np.random.default_rng(seed)
    mask = rng.random(depth_mm.shape) < keep_ratio
    out = depth_mm.copy()
    out[~mask] = 0
    return out


def estimate_metric_depth(
    image_rgb: np.ndarray,
    model_id: str,
    device: torch.device,
) -> np.ndarray:
    """Run Depth Anything V2 Metric; return depth in meters (H, W)."""
    try:
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation
    except ImportError as e:
        raise SystemExit(
            "transformers is required for monocular depth estimation.\n"
            "Install with: uv pip install transformers\n"
            f"Original error: {e}"
        ) from e

    from PIL import Image

    print(f"Loading depth model: {model_id}")
    processor = AutoImageProcessor.from_pretrained(model_id)
    model = AutoModelForDepthEstimation.from_pretrained(model_id).to(device)
    model.eval()

    pil = Image.fromarray(image_rgb)
    inputs = processor(images=pil, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs)
        predicted = outputs.predicted_depth

    h, w = image_rgb.shape[:2]
    depth = torch.nn.functional.interpolate(
        predicted.unsqueeze(1),
        size=(h, w),
        mode="bicubic",
        align_corners=False,
    ).squeeze().float().cpu().numpy()

    return depth


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare examples/<name> from an RGB-only image for LingBot-Depth",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--image", required=True, type=str, help="Path to RGB image")
    parser.add_argument(
        "--out",
        type=str,
        default="examples/my_scene",
        help="Output example directory (default: examples/my_scene)",
    )
    parser.add_argument(
        "--fov",
        type=float,
        default=65.0,
        help="Horizontal FOV in degrees when EXIF is missing (default: 65)",
    )
    parser.add_argument(
        "--use-exif",
        action="store_true",
        help="Prefer EXIF FocalLengthIn35mmFilm for intrinsics when present",
    )
    parser.add_argument(
        "--scene",
        choices=["indoor", "outdoor"],
        default="indoor",
        help="Metric depth model domain (default: indoor)",
    )
    parser.add_argument(
        "--depth-model",
        type=str,
        default=None,
        help=f"HF model id (default: {DEFAULT_MODEL} or outdoor Small)",
    )
    parser.add_argument(
        "--keep-ratio",
        type=float,
        default=1.0,
        help="Keep this fraction of depth pixels (rest=0) to fake sparse depth",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="auto",
        choices=["auto", "cuda", "cpu"],
        help="Device for depth estimation (default: auto)",
    )
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"Error: image not found: {image_path}")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    image_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image_bgr is None:
        print(f"Error: failed to read image: {image_path}")
        return 1

    h, w = image_bgr.shape[:2]
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

    # 1) RGB
    rgb_out = out_dir / "rgb.png"
    save_rgb(image_bgr, rgb_out)
    print(f"Saved RGB: {rgb_out} ({w}x{h})")

    # 2) Intrinsics
    K = None
    source = f"FOV={args.fov}°"
    if args.use_exif:
        K = intrinsics_from_exif(image_path, w, h)
        if K is not None:
            source = "EXIF FocalLengthIn35mmFilm"
    if K is None:
        K = approximate_intrinsics(w, h, args.fov)

    K_path = out_dir / "intrinsics.txt"
    write_intrinsics(K_path, K)
    print(f"Saved intrinsics ({source}): {K_path}")
    print(f"  fx={K[0, 0]:.2f} fy={K[1, 1]:.2f} cx={K[0, 2]:.2f} cy={K[1, 2]:.2f}")

    # 3) Metric depth → 16-bit mm PNG
    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)

    model_id = args.depth_model or (
        DEFAULT_MODEL if args.scene == "indoor" else OUTDOOR_MODEL
    )
    print(f"Estimating metric depth on {device}...")
    depth_m = estimate_metric_depth(image_rgb, model_id, device)
    depth_mm = depth_meters_to_uint16_mm(depth_m)
    depth_mm = sparsify_depth(depth_mm, args.keep_ratio)

    depth_out = out_dir / "raw_depth.png"
    cv2.imwrite(str(depth_out), depth_mm)

    valid = depth_mm > 0
    if valid.any():
        dmin, dmax = depth_mm[valid].min() / 1000.0, depth_mm[valid].max() / 1000.0
        print(f"Saved depth: {depth_out} ({dmin:.2f}–{dmax:.2f} m, keep_ratio={args.keep_ratio})")
    else:
        print(f"Warning: depth map has no valid pixels: {depth_out}")

    print("\nNext:")
    example_name = out_dir.name
    print(f"  uv run python example.py --example {example_name} --output result_{example_name}")
    print(
        "\nNote: monocular depth + approximate intrinsics are for qualitative demos only."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
