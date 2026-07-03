#!/usr/bin/env python3
"""Create compact transparent sticker GIFs without crossfade ghosting."""

from __future__ import annotations

import argparse
import importlib
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path


DEFAULT_DURATION = 3.0
DEFAULT_SIZE = 240
DEFAULT_MAX_BYTES = 500_000
DEFAULT_FPS = 10.0
DEFAULT_COLORS = 64
DEFAULT_OUTLINE_PX = 2.0
DEFAULT_PRE_SCALE = 3
DEFAULT_CORE_ERODE_PX = 1
DEFAULT_EDGE_THRESHOLD = 24
DEFAULT_MASK_BARRIER_PX = 2.7
DEFAULT_TEMP_FFMPEG_DIR = Path(tempfile.gettempdir()) / "codex_imageio_ffmpeg"
DEFAULT_TEMP_PILLOW_DIR = Path(tempfile.gettempdir()) / "codex_pillow"


Image = None
ImageFilter = None


def run(cmd: list[str], *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=False,
    )


def ensure_ffmpeg(no_install: bool = False) -> str:
    env_ffmpeg = os.environ.get("FFMPEG")
    if env_ffmpeg and Path(env_ffmpeg).exists():
        return env_ffmpeg

    path_ffmpeg = shutil.which("ffmpeg")
    if path_ffmpeg:
        return path_ffmpeg

    if str(DEFAULT_TEMP_FFMPEG_DIR) not in sys.path:
        sys.path.insert(0, str(DEFAULT_TEMP_FFMPEG_DIR))

    try:
        imageio_ffmpeg = importlib.import_module("imageio_ffmpeg")
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        if no_install:
            raise RuntimeError("ffmpeg not found. Set FFMPEG or install imageio-ffmpeg.")

    DEFAULT_TEMP_FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--target",
            str(DEFAULT_TEMP_FFMPEG_DIR),
            "imageio-ffmpeg",
        ],
    )
    importlib.invalidate_caches()
    imageio_ffmpeg = importlib.import_module("imageio_ffmpeg")
    return imageio_ffmpeg.get_ffmpeg_exe()


def ensure_pillow(no_install: bool = False) -> None:
    global Image, ImageFilter
    try:
        from PIL import Image as PILImage
        from PIL import ImageFilter as PILImageFilter

        Image = PILImage
        ImageFilter = PILImageFilter
        return
    except Exception:
        if no_install:
            raise RuntimeError("Pillow not found. Install Pillow or run without --no-install.")

    if str(DEFAULT_TEMP_PILLOW_DIR) not in sys.path:
        sys.path.insert(0, str(DEFAULT_TEMP_PILLOW_DIR))
    DEFAULT_TEMP_PILLOW_DIR.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--target",
            str(DEFAULT_TEMP_PILLOW_DIR),
            "Pillow",
        ],
    )
    importlib.invalidate_caches()
    from PIL import Image as PILImage
    from PIL import ImageFilter as PILImageFilter

    Image = PILImage
    ImageFilter = PILImageFilter


def parse_duration(ffmpeg: str, input_path: Path) -> float | None:
    proc = run([ffmpeg, "-hide_banner", "-i", str(input_path)], capture=True, check=False)
    text = (proc.stderr or b"").decode("utf-8", errors="ignore")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def scale_filter(size: int, fit: str) -> str:
    if fit == "stretch":
        return f"scale={size}:{size}:flags=lanczos"
    if fit == "contain":
        return (
            f"scale={size}:{size}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=white"
        )
    return f"scale={size}:{size}:force_original_aspect_ratio=increase:flags=lanczos,crop={size}:{size}"


def fps_label(fps: float) -> str:
    if abs(fps - round(fps)) < 1e-6:
        return str(int(round(fps)))
    return f"{fps:.6f}".rstrip("0").rstrip(".")


def odd_kernel_from_radius(radius_px: float) -> int:
    radius = max(0, int(round(radius_px)))
    return radius * 2 + 1


def frame_at(ffmpeg: str, input_path: Path, t: float, fit: str) -> bytes | None:
    proc = run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0, t):.4f}",
            "-i",
            str(input_path),
            "-frames:v",
            "1",
            "-vf",
            scale_filter(64, fit),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture=True,
        check=False,
    )
    data = proc.stdout or b""
    return data if len(data) == 64 * 64 * 3 else None


def mse(a: bytes, b: bytes) -> float:
    return sum((x - y) * (x - y) for x, y in zip(a, b)) / max(1, len(a))


def best_direct_start(
    ffmpeg: str,
    input_path: Path,
    duration: float,
    source_duration: float | None,
    fit: str,
    explicit_start: float | None,
) -> float:
    if explicit_start is not None:
        return max(0.0, explicit_start)
    if not source_duration or source_duration <= duration + 0.05:
        return 0.0

    max_start = max(0.0, source_duration - duration - 0.05)
    sample_count = min(61, max(2, int(max_start / 0.05) + 1))
    best = (float("inf"), 0.0)
    for idx in range(sample_count):
        start = max_start * idx / max(1, sample_count - 1)
        first = frame_at(ffmpeg, input_path, start, fit)
        last = frame_at(ffmpeg, input_path, start + duration, fit)
        if first is None or last is None:
            continue
        diff = mse(first, last)
        if diff < best[0]:
            best = (diff, start)
    return best[1]


def source_time_for_frame(idx: int, frame_count: int, fps: float, args: argparse.Namespace) -> float:
    if args.mode == "direct":
        return args.start + idx / fps
    phase = idx / frame_count
    triangle = 2 * phase if phase <= 0.5 else 2 - 2 * phase
    return args.start + args.segment * triangle


def extract_source_frames(
    ffmpeg: str,
    input_path: Path,
    raw_dir: Path,
    fps: float,
    args: argparse.Namespace,
) -> int:
    raw_dir.mkdir(parents=True, exist_ok=True)
    frame_count = int(round(args.duration * fps))
    source_size = args.size * args.pre_scale
    vf = scale_filter(source_size, args.fit)
    for idx in range(frame_count):
        source_t = source_time_for_frame(idx, frame_count, fps, args)
        out = raw_dir / f"frame_{idx:03d}.png"
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{source_t:.4f}",
                "-i",
                str(input_path),
                "-frames:v",
                "1",
                "-vf",
                vf,
                str(out),
            ]
        )
    return frame_count


def background_reference(im) -> tuple[int, int, int]:
    width, height = im.size
    samples = []
    for x in range(width):
        samples.append(im.getpixel((x, 0)))
        samples.append(im.getpixel((x, height - 1)))
    for y in range(height):
        samples.append(im.getpixel((0, y)))
        samples.append(im.getpixel((width - 1, y)))
    return tuple(sorted(pixel[channel] for pixel in samples)[len(samples) // 2] for channel in range(3))


def strong_foreground(pixel: tuple[int, int, int], bg: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    max_channel = max(pixel)
    min_channel = min(pixel)
    saturation = max_channel - min_channel
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    distance = math.sqrt(sum((pixel[channel] - bg[channel]) ** 2 for channel in range(3)))
    return saturation > 46 or luminance < 168 or distance > 72


def foreground_mask(rgb, args: argparse.Namespace):
    rgb = rgb.convert("RGB")
    width, height = rgb.size
    bg = background_reference(rgb)
    pixels = rgb.load()

    barrier = Image.new("L", (width, height), 0)
    barrier_pixels = barrier.load()
    for y in range(height):
        for x in range(width):
            if strong_foreground(pixels[x, y], bg):
                barrier_pixels[x, y] = 255

    barrier_radius = args.mask_barrier_px * args.pre_scale
    barrier = barrier.filter(ImageFilter.MaxFilter(odd_kernel_from_radius(barrier_radius)))
    barrier_pixels = barrier.load()

    outside = [[False] * width for _ in range(height)]
    queue = deque()
    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        if outside[y][x] or barrier_pixels[x, y] > 0:
            continue
        outside[y][x] = True
        if x > 0:
            queue.append((x - 1, y))
        if x < width - 1:
            queue.append((x + 1, y))
        if y > 0:
            queue.append((x, y - 1))
        if y < height - 1:
            queue.append((x, y + 1))

    mask = Image.new("L", (width, height), 0)
    mask_pixels = mask.load()
    for y in range(height):
        for x in range(width):
            if not outside[y][x]:
                mask_pixels[x, y] = 255

    return mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))


def threshold_alpha(alpha, threshold: int):
    return alpha.point(lambda value: 255 if value >= threshold else 0)


def make_processed_frame(src_path: Path, dst_path: Path, args: argparse.Namespace) -> None:
    source = Image.open(src_path).convert("RGB")
    target_size = (args.size, args.size)
    resample = Image.Resampling.LANCZOS

    if args.style == "plain":
        source.resize(target_size, resample).save(dst_path)
        return

    mask = foreground_mask(source, args)
    core = mask
    for _ in range(max(0, int(args.core_erode_px * args.pre_scale))):
        core = core.filter(ImageFilter.MinFilter(3))

    core_small = threshold_alpha(core.resize(target_size, resample), args.edge_threshold)
    rgb_small = source.resize(target_size, resample).convert("RGBA")

    if args.style == "transparent":
        rgb_small.putalpha(core_small)
        rgb_small.save(dst_path)
        return

    outline = core
    for _ in range(max(0, int(round(args.outline_px * args.pre_scale)))):
        outline = outline.filter(ImageFilter.MaxFilter(3))
    outline_small = threshold_alpha(outline.resize(target_size, resample), args.edge_threshold)

    sticker = Image.new("RGBA", target_size, (255, 255, 255, 0))
    white = Image.new("RGBA", target_size, (255, 255, 255, 255))
    sticker.paste(white, (0, 0), outline_small)
    rgb_small.putalpha(core_small)
    sticker.alpha_composite(rgb_small)
    sticker.save(dst_path)


def process_frames(raw_dir: Path, frames_dir: Path, args: argparse.Namespace) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(raw_dir.glob("frame_*.png")):
        make_processed_frame(src, frames_dir / src.name, args)


def color_candidates(max_colors: int) -> list[int]:
    options = [max_colors, 56, 48, 40, 36, 32, 24, 20, 18, 16, 14, 12, 8]
    return [color for index, color in enumerate(options) if color <= max_colors and color not in options[:index]]


def encode_gif(ffmpeg: str, frames_dir: Path, fps: float, colors: int, out: Path, args: argparse.Namespace) -> None:
    if args.style == "plain":
        palette = (
            f"[0:v]split[s0][s1];"
            f"[s0]palettegen=max_colors={colors}:stats_mode=diff[p];"
            f"[s1][p]paletteuse=dither=bayer:bayer_scale={args.bayer_scale}:diff_mode=rectangle"
        )
    else:
        palette = (
            f"[0:v]split[s0][s1];"
            f"[s0]palettegen=max_colors={colors}:reserve_transparent=1:stats_mode=diff[p];"
            f"[s1][p]paletteuse=dither=bayer:bayer_scale={args.bayer_scale}:"
            f"diff_mode=rectangle:alpha_threshold={args.edge_threshold}"
        )

    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-framerate",
            fps_label(fps),
            "-i",
            str(frames_dir / "frame_%03d.png"),
            "-filter_complex",
            palette,
            "-map_metadata",
            "-1",
            "-loop",
            "0",
            str(out),
        ]
    )


def default_output_path(input_path: Path, size: int) -> Path:
    return input_path.with_name(f"{input_path.stem}_3s_{size}_sticker.gif")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create compact no-ghost sticker GIFs.")
    parser.add_argument("input", type=Path, help="Input video path")
    parser.add_argument("--output", type=Path, help="Output GIF path")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION, help="Output duration in seconds")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE, help="Square output size in pixels")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="Maximum GIF size in bytes")
    parser.add_argument("--mode", choices=["pingpong", "direct"], default="pingpong", help="Loop strategy")
    parser.add_argument("--style", choices=["sticker", "transparent", "plain"], default="sticker", help="Visual postprocess style")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help="Output frame rate. Use 10 for uniform 100 ms GIF delays.")
    parser.add_argument("--colors", type=int, default=DEFAULT_COLORS, help="Maximum palette colors before transparent reserve")
    parser.add_argument("--start", type=float, help="Source start time in seconds")
    parser.add_argument("--segment", type=float, help="Source span for ping-pong mode")
    parser.add_argument("--fit", choices=["crop", "contain", "stretch"], default="crop", help="How to fit non-square sources")
    parser.add_argument("--outline-px", type=float, default=DEFAULT_OUTLINE_PX, help="Sticker white outline thickness in output pixels")
    parser.add_argument("--pre-scale", type=int, default=DEFAULT_PRE_SCALE, help="Process frames at N times output size for cleaner edges")
    parser.add_argument("--core-erode-px", type=int, default=DEFAULT_CORE_ERODE_PX, help="Shrink foreground before outlining to reduce gray edge halos")
    parser.add_argument("--edge-threshold", type=int, default=DEFAULT_EDGE_THRESHOLD, help="Alpha threshold used for GIF transparency")
    parser.add_argument("--mask-barrier-px", type=float, default=DEFAULT_MASK_BARRIER_PX, help="Foreground barrier size used by background flood fill")
    parser.add_argument("--bayer-scale", type=int, default=4, choices=range(0, 6), help="Bayer dither scale for paletteuse")
    parser.add_argument("--keep-work", action="store_true", help="Keep temporary frames and candidate GIFs")
    parser.add_argument("--no-install", action="store_true", help="Do not install missing ffmpeg/Pillow helpers into /tmp")
    return parser.parse_args()


def normalize_args(args: argparse.Namespace, source_duration: float | None, fit: str) -> None:
    explicit_start = args.start
    args.start = max(0.0, args.start or 0.0)
    if args.mode == "direct":
        args.start = best_direct_start(args.ffmpeg, args.input_path, args.duration, source_duration, fit, explicit_start)
        args.segment = 0.0
        return

    args.segment = args.segment or (args.duration / 2.0)
    if source_duration:
        available = max(0.1, source_duration - args.start - 0.05)
        args.segment = min(args.segment, available)


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 2

    args.input_path = input_path
    output_path = (args.output or default_output_path(input_path, args.size)).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = ensure_ffmpeg(no_install=args.no_install)
    args.ffmpeg = ffmpeg
    ensure_pillow(no_install=args.no_install)
    source_duration = parse_duration(ffmpeg, input_path)
    normalize_args(args, source_duration, args.fit)

    work_dir = Path(tempfile.mkdtemp(prefix="meme_postprocess_"))
    try:
        selected = None
        raw_dir = work_dir / f"raw_{fps_label(args.fps)}"
        frame_count = extract_source_frames(ffmpeg, input_path, raw_dir, args.fps, args)
        frames_dir = work_dir / f"{args.style}_{fps_label(args.fps)}"
        process_frames(raw_dir, frames_dir, args)

        candidates = []
        for colors in color_candidates(max(2, min(256, args.colors))):
            out = work_dir / f"candidate_{args.style}_{fps_label(args.fps)}_{colors}.gif"
            encode_gif(ffmpeg, frames_dir, args.fps, colors, out, args)
            candidates.append((colors, out))
            if out.stat().st_size <= args.max_bytes:
                selected = (colors, out)
                break

        if selected is None:
            smallest = min(candidates, key=lambda item: item[1].stat().st_size)
            print(
                f"No candidate fit under {args.max_bytes} bytes. "
                f"Smallest was {smallest[1].stat().st_size} bytes with {smallest[0]} colors.",
                file=sys.stderr,
            )
            return 1

        colors, selected_path = selected
        shutil.copyfile(selected_path, output_path)
        final_size = output_path.stat().st_size
        delay_ms = int(round(1000 / args.fps))
        print(f"output={output_path}")
        print(f"bytes={final_size}")
        print(f"duration={args.duration:.3f}s")
        print(f"size={args.size}x{args.size}")
        print(f"mode={args.mode}")
        print(f"style={args.style}")
        print(f"fps={fps_label(args.fps)}")
        print(f"frames={frame_count}")
        print(f"delay_ms~{delay_ms}")
        print(f"colors={colors}")
        print(f"outline_px={args.outline_px if args.style == 'sticker' else 0}")
        print("transparent=yes" if args.style != "plain" else "transparent=no")
        print("loop=infinite")
        return 0
    finally:
        if args.keep_work:
            print(f"work_dir={work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
