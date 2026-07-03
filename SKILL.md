---
name: emoji-gif-make
description: emoji gif make workflow for converting sticker, meme, emoji, and reaction videos into compact transparent sticker-style looping GIFs. Use when the user asks to process 表情包, make a 3-second GIF, resize to 240x240, keep under 500 KB, create transparent background, add clean sticker outline, reduce jagged edges, remove loop stutter, avoid ghosting, or batch-postprocess sticker videos.
---

# emoji gif make

## Defaults

Use these defaults unless the user overrides them:

- Output: GIF89a, infinite loop.
- Duration: 3.0 seconds.
- Canvas: 240 x 240 px.
- Size cap: 500,000 bytes, conservative interpretation of 500 KB.
- Style: transparent sticker GIF with a thin white outline.
- Edge process: extract/process at 3x resolution, then downsample to reduce jagged edges.
- Default cadence: 30 frames at 10 fps, uniform 100 ms GIF delays.
- Default palette: 64 colors, automatically lowering colors only when needed to stay under the size cap.
- Loop style: ping-pong loop, meaning forward then reverse, when the source is not already seamless.

Never use `xfade`, dissolve, motion-blend, or frame interpolation to hide a loop cut unless the user explicitly accepts possible ghosting. Ghosting is worse than a small cadence change for sticker use.

## Quick Start

Run the bundled script first:

```bash
python3 scripts/make_gif.py INPUT_VIDEO --output OUTPUT_GIF
```

For the common request "turn this video into a 3-second 240 x 240 transparent sticker GIF under 500 KB":

```bash
python3 scripts/make_gif.py \
  /path/to/input.mp4 \
  --output /path/to/output.gif \
  --duration 3 \
  --size 240 \
  --max-bytes 500000 \
  --style sticker \
  --outline-px 2 \
  --fps 10 \
  --colors 64 \
  --mode pingpong
```

The script obtains `ffmpeg` from `FFMPEG`, `PATH`, or a temporary `imageio-ffmpeg` install under `/tmp`. It also uses Pillow for sticker masking; if Pillow is missing, the script installs it under `/tmp` rather than into the system Python environment.

## Workflow

1. Inspect the user request and identify input videos and desired outputs.
2. Prefer default `--style sticker --outline-px 2` for clean transparent sticker output. This makes edges look intentional and hides residual background halos.
3. Prefer `--mode pingpong` for a smooth no-ghosting loop when the user cares about "丝滑", "不卡顿", "无重影", or does not require one-way motion.
4. Use `--mode direct` only when the source is already intended to loop forward-only, or the user says not to reverse the motion. Direct mode searches for the best 3-second window but may still show a hard loop if the source has no matching boundary.
5. Use `--style transparent` only when the user wants no sticker outline. Use `--style plain` only when the user wants a normal square GIF with no transparency.
6. Use `--fit crop` by default for square sticker canvases. Use `--fit contain` if preserving the entire non-square frame matters more than filling the canvas.
7. After export, verify all constraints: exact pixel size, total duration, infinite loop, transparent corners for sticker/transparent outputs, uniform GIF delays where possible, and file size under the cap.
8. Show or link the final GIF and mention the actual size, duration, frame count, color count, transparency, outline style, and loop method.

## Quality Rules

- Prefer the sticker default over raw transparency when source backgrounds are light gray/white; the thin outline keeps edges clean.
- Keep frame delays uniform when possible. The house default is 30 frames at 100 ms because GIF centisecond timing represents it exactly.
- Keep 64 colors when it fits. Reduce colors before changing duration, canvas size, or transparency.
- Keep the canvas at 240 x 240 unless the user changes it.
- Strip metadata from the GIF.
- Keep temporary candidates hidden or in `/tmp`; delete failed candidates before finishing.
- If no candidate fits, report the closest file size and ask which constraint can move: larger file cap, fewer colors, fewer frames, shorter duration, smaller canvas, or lighter sticker edge.

## Script Notes

Use `scripts/make_gif.py` rather than retyping ffmpeg/Pillow command chains. Read or patch the script only if the requested output differs materially from the defaults.

Important options:

- `--style sticker`: transparent background with thin white sticker outline; default.
- `--style transparent`: transparent background without white outline.
- `--style plain`: no transparency and no sticker outline.
- `--outline-px 2`: default thin white outline thickness.
- `--pre-scale 3`: process masks at 3x resolution before downsampling to reduce jagged edges.
- `--mode pingpong`: forward then reverse, no crossfade, best for seamless no-ghost loops.
- `--mode direct`: one-way clip, no fade, best when reverse motion is undesirable.
- `--start SECONDS`: choose a source start point.
- `--segment SECONDS`: source span for ping-pong mode; defaults to half the output duration.
- `--fit crop|contain|stretch`: control non-square source framing.
- `--keep-work`: keep temporary frame/candidate files for debugging.
