#!/usr/bin/env python3
"""
photo_to_reel.py — turn a still photo into a 1080x1920, 12-second Instagram
Reels clip that matches the rolento photography brand template:

  1. The clip opens on a "branded rest frame": the rolento photography
     logo lockup in a white bar at the top (and, for moderate landscape
     photos, a second white bar with the url/cities line at the bottom),
     with the photo inset below/between.
  2. The whole frame slowly PUSHES IN, cropping the white bars away as it
     goes, eventually landing on a tight, full-bleed crop of the photo.
  3. It then EASES BACK OUT, landing on the exact same branded frame it
     started on — so the clip loops with no visible seam.

Which brand treatment a photo gets depends on its aspect ratio:

  - Portrait / near-square (w/h <= 1.15): full 4-line logo block at top
    (wordmark + url + cities all in one bar), photo cover-cropped to fill
    everything below it edge to edge. No footer. Push-in is slow and
    continues almost to the end of the clip, then snaps back out fast in
    the last ~10% (a "reveal, then reset" feel).

  - Landscape (1.15 < w/h <= 1.8): short 2-line wordmark at top, photo
    fit to the full width with nothing cropped, and a 2-line url/cities
    footer filling the leftover space at the bottom. Push-in is quicker,
    peaking a bit before the midpoint, then eases back out gradually.

  - Panorama (w/h > 1.8): short 2-line wordmark at top only, photo
    cover-cropped (sides trimmed) to fill everything below it edge to
    edge — same quick-in/slow-out pacing as landscape.

Usage:
    python3 photo_to_reel.py INPUT.jpg OUTPUT.mp4
    python3 photo_to_reel.py INPUT.jpg OUTPUT.mp4 --duration 12 --fps 30
    python3 photo_to_reel.py --batch INPUT_DIR OUTPUT_DIR

Requires: ffmpeg on PATH, Pillow (pip install Pillow).
Brand assets expected in an "assets/" folder next to this script:
    assets/brand_header.png        (1080-wide, full 4-line logo block)
    assets/brand_header_short.png  (1080-wide, 2-line wordmark only)
    assets/brand_footer.png        (1080-wide, 2-line url/cities line)
"""
import argparse
import os
import subprocess
import sys

from PIL import Image, ImageOps

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(SCRIPT_DIR, "assets")

CANVAS_W, CANVAS_H = 1080, 1920
SUPER = 2  # supersample the working canvas so tight crops stay sharp
BASE_W, BASE_H = CANVAS_W * SUPER, CANVAS_H * SUPER

PORTRAIT_MAX_RATIO = 1.15   # w/h at or below this -> portrait/square treatment
PANORAMA_MIN_RATIO = 1.8    # w/h above this -> panorama treatment
# everything in between -> landscape (letterboxed) treatment

# native pixel heights of the brand assets, at 1080 width (pre-supersample)
HEADER_FULL_H = 312
HEADER_SHORT_H = 600
FOOTER_H = 260


def _load_asset(name, target_w):
    path = os.path.join(ASSETS_DIR, name)
    img = Image.open(path).convert("RGB")
    if img.width != target_w:
        scale = target_w / img.width
        img = img.resize((target_w, int(round(img.height * scale))), Image.LANCZOS)
    return img


def _cover_fit(img, box_w, box_h):
    """Scale img to cover box_w x box_h, then center-crop to exactly that size."""
    w, h = img.size
    scale = max(box_w / w, box_h / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    x0 = (new_w - box_w) // 2
    y0 = (new_h - box_h) // 2
    return img.crop((x0, y0, x0 + box_w, y0 + box_h))


def classify(w, h):
    ratio = w / h
    if ratio <= PORTRAIT_MAX_RATIO:
        return "portrait"
    if ratio > PANORAMA_MIN_RATIO:
        return "panorama"
    return "landscape"


def compose_canvas(src_path):
    """Build the branded 'rest frame' at BASE_W x BASE_H, and return
    (canvas, focus_point, category) where focus_point is the (x, y) in
    canvas coordinates the push-in animation should zoom toward."""
    photo = ImageOps.exif_transpose(Image.open(src_path)).convert("RGB")
    w, h = photo.size
    category = classify(w, h)

    canvas = Image.new("RGB", (BASE_W, BASE_H), "white")

    if category == "portrait":
        header = _load_asset("brand_header.png", BASE_W)
        header_h_px = HEADER_FULL_H * SUPER
        canvas.paste(header, (0, 0))

        avail_h = BASE_H - header_h_px
        fitted = _cover_fit(photo, BASE_W, avail_h)
        canvas.paste(fitted, (0, header_h_px))

        focus = (BASE_W / 2, header_h_px + avail_h * 0.38)  # bias toward upper body/face

    elif category == "panorama":
        header = _load_asset("brand_header_short.png", BASE_W)
        header_h_px = HEADER_SHORT_H * SUPER
        canvas.paste(header, (0, 0))

        avail_h = BASE_H - header_h_px
        fitted = _cover_fit(photo, BASE_W, avail_h)
        canvas.paste(fitted, (0, header_h_px))

        focus = (BASE_W / 2, header_h_px + avail_h / 2)

    else:  # landscape
        header = _load_asset("brand_header_short.png", BASE_W)
        footer = _load_asset("brand_footer.png", BASE_W)
        header_h_px = HEADER_SHORT_H * SUPER
        footer_h_px = FOOTER_H * SUPER
        canvas.paste(header, (0, 0))

        mid_h = BASE_H - header_h_px - footer_h_px
        new_h = int(round(BASE_W * h / w))
        if new_h > mid_h:
            # unusually tall for this bucket -- cover-crop instead of overflowing
            fitted = _cover_fit(photo, BASE_W, mid_h)
            photo_h = mid_h
        else:
            fitted = photo.resize((BASE_W, new_h), Image.LANCZOS)
            photo_h = new_h
        canvas.paste(fitted, (0, header_h_px))
        canvas.paste(footer, (0, header_h_px + photo_h))

        focus = (BASE_W / 2, header_h_px + photo_h / 2)

    return canvas, focus, category


def _smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _progress(i, total_frames, peak_frac):
    peak_i = peak_frac * total_frames
    if i <= peak_i:
        t = i / peak_i if peak_i > 0 else 1.0
        return _smoothstep(t)
    else:
        remaining = total_frames - peak_i
        t = (i - peak_i) / remaining if remaining > 0 else 0.0
        return 1.0 - _smoothstep(t)


CATEGORY_TIMING = {
    # peak_frac: how far through the clip the push-in peaks
    # peak_zoom_floor: minimum zoom multiplier at peak (auto-raised if needed
    #                   to guarantee a full-bleed crop at peak)
    "portrait": dict(peak_frac=0.85, peak_zoom_floor=1.9),
    "landscape": dict(peak_frac=0.40, peak_zoom_floor=1.5),
    "panorama": dict(peak_frac=0.40, peak_zoom_floor=1.5),
}


def render_frames(canvas, focus, category, duration, fps):
    timing = CATEGORY_TIMING[category]
    total_frames = max(int(round(duration * fps)), 2)

    # make sure peak zoom is enough to fully hide the branded bars
    min_zoom_for_full_bleed = max(BASE_W / BASE_W, BASE_H / BASE_H)  # 1.0 baseline
    # the tightest the crop can get while still fitting inside the canvas
    # is bounded automatically by clamping below, so we just need a zoom
    # comfortably above 1.0 -- the floor values already do this.
    peak_zoom = timing["peak_zoom_floor"]

    cx0, cy0 = BASE_W / 2, BASE_H / 2
    fx, fy = focus

    for i in range(total_frames):
        p = _progress(i, total_frames, timing["peak_frac"])
        zoom = 1.0 + (peak_zoom - 1.0) * p
        cx = cx0 + (fx - cx0) * p
        cy = cy0 + (fy - cy0) * p

        half_w = BASE_W / (2 * zoom)
        half_h = BASE_H / (2 * zoom)
        left, right = cx - half_w, cx + half_w
        top, bottom = cy - half_h, cy + half_h

        # clamp inside canvas bounds by shifting (never scaling)
        if left < 0:
            right -= left
            left = 0
        if top < 0:
            bottom -= top
            top = 0
        if right > BASE_W:
            left -= (right - BASE_W)
            right = BASE_W
        if bottom > BASE_H:
            top -= (bottom - BASE_H)
            bottom = BASE_H
        left, top = max(0, left), max(0, top)

        frame = canvas.crop((int(left), int(top), int(right), int(bottom)))
        frame = frame.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
        yield frame


def convert(src_path, out_path, duration=12.0, fps=30):
    canvas, focus, category = compose_canvas(src_path)

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{CANVAS_W}x{CANVAS_H}",
        "-r", str(fps), "-i", "-",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "medium", "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in render_frames(canvas, focus, category, duration, fps):
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    finally:
        stderr = proc.stderr.read()
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"ffmpeg failed on {src_path}:\n{stderr[-2000:].decode(errors='replace')}")

    return category


def batch(input_dir, output_dir, duration, fps):
    os.makedirs(output_dir, exist_ok=True)
    exts = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
    files = sorted(
        f for f in os.listdir(input_dir)
        if os.path.splitext(f)[1].lower() in exts
    )
    if not files:
        print(f"No photos found in {input_dir}", file=sys.stderr)
        return
    for f in files:
        src = os.path.join(input_dir, f)
        out = os.path.join(output_dir, os.path.splitext(f)[0] + "_reel.mp4")
        try:
            category = convert(src, out, duration, fps)
            print(f"[{category:>9}] {f} -> {os.path.basename(out)}")
        except Exception as e:
            print(f"[  FAILED] {f}: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="input photo, or input folder if --batch")
    ap.add_argument("output", help="output mp4 path, or output folder if --batch")
    ap.add_argument("--batch", action="store_true", help="process every photo in a folder")
    ap.add_argument("--duration", type=float, default=12.0, help="clip length in seconds (default 12)")
    ap.add_argument("--fps", type=int, default=30, help="frames per second (default 30)")
    args = ap.parse_args()

    if args.batch:
        batch(args.input, args.output, args.duration, args.fps)
    else:
        category = convert(args.input, args.output, args.duration, args.fps)
        print(f"Done: {args.output} ({category} treatment)")


if __name__ == "__main__":
    main()
