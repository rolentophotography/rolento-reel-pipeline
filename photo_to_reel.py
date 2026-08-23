#!/usr/bin/env python3
"""
photo_to_reel.py — turn a still photo into a 1080x1920, 12-second Instagram
Reels clip that matches the rolento photography brand template:

  1. The clip opens on a "branded rest frame": the rolento photography
     logo lockup in a white bar at the top (and a second white bar with
     the url/cities line at the bottom, for landscape/panorama photos),
     with the FULL, UNCROPPED photo shown between them, scaled to fit
     (never cropped) -- so if the photo's own aspect ratio leaves extra
     room, that shows as plain white space, never a cut-off edge.
  2. The photo (and ONLY the photo) then grows to fill the entire
     1080x1920 frame edge to edge, covering the header/footer as it goes
     -- while the crop simultaneously tightens/slides. The header/footer
     bars themselves NEVER move, scale, or distort; they are static,
     fixed-position art pasted underneath the photo layer every frame,
     so they are either fully crisp or fully covered -- never warped
     mid-zoom.
  3. For landscape/panorama photos, once full-frame, the crop pans
     horizontally starting at the true left edge of the source photo and
     ending at the true right edge. Portrait photos instead continue
     zooming in tighter, biased toward the upper body/face.
  4. The photo box then shrinks back down to the exact same branded rest
     frame the clip opened on -- so the clip loops with no visible seam.

Which brand treatment a photo gets depends on its aspect ratio:

  - Portrait / near-square (w/h <= 1.15): full 4-line logo block at top
    (wordmark + url + cities all in one bar). No footer.
  - Landscape (1.15 < w/h <= 1.8) and Panorama (w/h > 1.8): short 2-line
    wordmark at top, 2-line url/cities footer at the bottom. Panoramas
    just end up with more visible white space at rest since the fitted
    photo is relatively shorter.

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

PORTRAIT_MAX_RATIO = 1.15   # w/h at or below this -> portrait/square treatment
PANORAMA_MIN_RATIO = 1.8    # w/h above this -> panorama treatment
# everything in between -> landscape treatment (panorama uses the same bar
# layout as landscape, just ends up more letterboxed at rest)

# native pixel heights of the brand assets, at 1080 width
HEADER_FULL_H = 312
HEADER_SHORT_H = 600
FOOTER_H = 260
FOOTER_BOTTOM_MARGIN = 90   # keeps the footer words out of the very bottom
                            # edge of the frame (Reels UI safe zone)

# The animation treats the photo as a RIGID object: one rectangle (position
# + uniform scale, always the photo's own aspect ratio) that moves through
# keyframes, and whatever part of it falls outside the 1080x1920 canvas is
# simply clipped by the frame edges. Nothing inside the frame is ever
# cropped away or distorted -- content only disappears by physically
# sliding/spilling out of the frame:
#   portrait             -> enlarges with its bottom edge pinned to the
#                           bottom of the frame (the top spills out of the
#                           frame as it grows), keeps zooming past a plain
#                           cover-fit, then pans UP without resizing until
#                           the very top of the photo shows, then shrinks
#                           back to the rest frame.
#   landscape / panorama -> enlarges until the photo's height fills the
#                           frame with its true LEFT edge at the frame's
#                           left, pans horizontally to its true RIGHT edge,
#                           then shrinks back to the rest frame.
PORTRAIT_PEAK_EXTRA_ZOOM = 1.5   # how much tighter than a plain cover-fit
                                  # the portrait zoom goes before panning up

# Timeline fractions (of the whole clip) for each phase boundary:
#   0 .. hold1   : HOLD the branded rest frame (photo + words fully visible)
#   hold1 .. grow: photo grows from rest to full-bleed
#   grow .. pan  : photo pans across its long axis at full size
#   pan .. shrink: photo shrinks back down to the rest frame
#   shrink .. 1  : HOLD the rest frame again (so the words are readable at
#                  the end too, and the loop point sits inside a still frame)
# These match the pacing of the reference sample reels, which sit on the
# branded frame for a good moment before and after the move.
CATEGORY_TIMING = {
    "portrait":  dict(hold1_frac=0.13, grow_frac=0.28, pan_frac=0.70, shrink_frac=0.87),
    "landscape": dict(hold1_frac=0.13, grow_frac=0.28, pan_frac=0.70, shrink_frac=0.87),
    "panorama":  dict(hold1_frac=0.13, grow_frac=0.28, pan_frac=0.70, shrink_frac=0.87),
}


def _load_asset(name, target_w=CANVAS_W):
    path = os.path.join(ASSETS_DIR, name)
    img = Image.open(path).convert("RGB")
    if img.width != target_w:
        scale = target_w / img.width
        img = img.resize((target_w, int(round(img.height * scale))), Image.LANCZOS)
    return img


def classify(w, h):
    ratio = w / h
    if ratio <= PORTRAIT_MAX_RATIO:
        return "portrait"
    if ratio > PANORAMA_MIN_RATIO:
        return "panorama"
    return "landscape"


def _word_rows(img, min_dark_cols=5):
    """(top_row, bottom_row) of the actually-visible words in a brand
    asset. The threshold is strict (near-black) because the assets carry
    faint gray video-compression noise along their edges that must NOT
    count as content -- only the actual dark lettering should."""
    gray = img.convert("L")
    w, h = gray.size
    px = gray.load()
    rows = [y for y in range(h)
            if sum(1 for x in range(0, w, 4) if px[x, y] < 120) >= min_dark_cols]
    if not rows:
        return 0, h
    return rows[0], rows[-1] + 1


def _visual_box(header_img, footer_img):
    """Returns (box_top, box_bottom) in canvas pixels: the span between the
    header's lowest visible word pixel and the footer's highest visible
    word pixel (or the canvas bottom when there is no footer). Centering
    the photo in THIS box makes the white gap above the photo and the
    white gap below it exactly equal to the eye."""
    _, header_words_end = _word_rows(header_img)
    if footer_img is None:
        return header_words_end, CANVAS_H
    footer_y = CANVAS_H - footer_img.height - FOOTER_BOTTOM_MARGIN
    footer_words_start, _ = _word_rows(footer_img)
    return header_words_end, footer_y + footer_words_start


def _lerp_rect(r0, r1, p):
    return tuple(a + (b - a) * p for a, b in zip(r0, r1))


def _pace(t):
    """Linear pacing (no ease-in/out) -- a constant-speed zoom/pan, per
    direct request: the grow/pan/shrink should read as a plain linear
    move, not an eased one."""
    return max(0.0, min(1.0, t))


FRAME_CENTER_Y = CANVAS_H / 2   # 960: the vertical center of the reel frame
REST_PAD = 16                   # min breathing room between photo and words


def _rest_states(pw, ph, box_top, box_bottom, frame_centered):
    """The photo at rest: shown in full (never cropped), scaled to fit.

    frame_centered=True (landscape/panorama): the CENTER POINT of the
    photo is anchored to the CENTER of the frame (y=960) -- not centered
    inside the words box, whose own middle sits lower because the header
    is taller than the footer. The size is capped so the photo cannot
    reach whichever words line is closer to the frame center.

    frame_centered=False (portrait): centered inside the box between the
    header and the canvas bottom, as before."""
    if frame_centered:
        half_room = min(FRAME_CENTER_Y - box_top, box_bottom - FRAME_CENTER_Y) - REST_PAD
        fit_scale = min(CANVAS_W / pw, (2 * half_room) / ph)
        fit_w, fit_h = pw * fit_scale, ph * fit_scale
        dx = (CANVAS_W - fit_w) / 2
        dy = FRAME_CENTER_Y - fit_h / 2
    else:
        box_h = box_bottom - box_top
        fit_scale = min(CANVAS_W / pw, box_h / ph)
        fit_w, fit_h = pw * fit_scale, ph * fit_scale
        dx = (CANVAS_W - fit_w) / 2
        dy = box_top + (box_h - fit_h) / 2
    return (0, 0, pw, ph), (dx, dy, fit_w, fit_h)


def _keyframes(category, pw, ph, rest_dst):
    """The three keyframe rects of the photo's journey (all share the
    photo's own aspect ratio, which is what guarantees zero distortion):
    rest -> peak_a (start of the pan) -> peak_b (end of the pan)."""
    if category == "portrait":
        cover = max(CANVAS_W / pw, CANVAS_H / ph)
        s1 = cover * PORTRAIT_PEAK_EXTRA_ZOOM
        w1, h1 = pw * s1, ph * s1
        peak_a = ((CANVAS_W - w1) / 2, CANVAS_H - h1, w1, h1)  # bottom of photo at frame bottom
        peak_b = ((CANVAS_W - w1) / 2, 0.0, w1, h1)            # top of photo at frame top
    else:
        s1 = CANVAS_H / ph
        w1 = pw * s1
        peak_a = (0.0, 0.0, w1, CANVAS_H)             # true left edge at frame left
        peak_b = (CANVAS_W - w1, 0.0, w1, CANVAS_H)   # true right edge at frame right
    return rest_dst, peak_a, peak_b


def _rect_at(timing, rest, peak_a, peak_b, i, total_frames):
    """The photo's (possibly frame-overflowing) rect for frame i."""
    tt = i / (total_frames - 1) if total_frames > 1 else 0.0
    phase, p = _phase(timing, tt)
    if phase == "grow":
        return _lerp_rect(rest, peak_a, p)
    if phase == "pan":
        return _lerp_rect(peak_a, peak_b, p)
    if phase == "shrink":
        return _lerp_rect(peak_b, rest, p)
    return rest                                   # hold1 / hold2


def _visible_src_dst(pw, ph, rect):
    """Clip the photo's rect against the canvas. Returns (src, dst):
    which part of the source photo is inside the frame, and where it
    lands on the canvas. Because the rect always has the photo's own
    aspect ratio, x and y scale factors are identical -- a pure uniform
    zoom, never a stretch."""
    x, y, w, h = rect
    vx0, vy0 = max(x, 0.0), max(y, 0.0)
    vx1, vy1 = min(x + w, CANVAS_W), min(y + h, CANVAS_H)
    fx, fy = pw / w, ph / h
    src = ((vx0 - x) * fx, (vy0 - y) * fy,
           (vx1 - vx0) * fx, (vy1 - vy0) * fy)
    dst = (vx0, vy0, vx1 - vx0, vy1 - vy0)
    return src, dst


def analyze(src_path):
    photo = ImageOps.exif_transpose(Image.open(src_path)).convert("RGB")
    w, h = photo.size
    category = classify(w, h)
    return photo, category


def _phase(timing, tt):
    """Map clip position tt (0..1) to (phase_name, progress 0..1)."""
    h1, gf = timing["hold1_frac"], timing["grow_frac"]
    pf, sf = timing["pan_frac"], timing["shrink_frac"]
    if tt <= h1:
        return "hold1", 0.0
    if tt <= gf:
        return "grow", _pace((tt - h1) / (gf - h1)) if gf > h1 else 1.0
    if tt <= pf:
        return "pan", _pace((tt - gf) / (pf - gf)) if pf > gf else 1.0
    if tt <= sf:
        return "shrink", _pace((tt - pf) / (sf - pf)) if sf > pf else 1.0
    return "hold2", 0.0


def render_frames(photo, category, duration, fps, header_img, footer_img):
    timing = CATEGORY_TIMING[category]
    box_top, box_bottom = _visual_box(header_img, footer_img)
    total_frames = max(int(round(duration * fps)), 2)
    pw, ph = photo.size

    rest_src, rest_dst = _rest_states(
        pw, ph, box_top, box_bottom, frame_centered=(category != "portrait")
    )
    rest, peak_a, peak_b = _keyframes(category, pw, ph, rest_dst)

    for i in range(total_frames):
        rect = _rect_at(timing, rest, peak_a, peak_b, i, total_frames)
        src, dst = _visible_src_dst(pw, ph, rect)
        sx, sy, sw, sh = src
        dx, dy, dw, dh = dst
        dw_i, dh_i = max(int(round(dw)), 1), max(int(round(dh)), 1)

        cropped = photo.crop((
            int(round(sx)), int(round(sy)),
            int(round(sx + sw)), int(round(sy + sh)),
        ))
        resized = cropped.resize((dw_i, dh_i), Image.LANCZOS)

        frame = Image.new("RGB", (CANVAS_W, CANVAS_H), "white")
        frame.paste(header_img, (0, 0))
        if footer_img is not None:
            frame.paste(footer_img, (0, CANVAS_H - footer_img.height - FOOTER_BOTTOM_MARGIN))
        frame.paste(resized, (int(round(dx)), int(round(dy))))  # photo on top, covers bars as it grows
        yield frame


def _assets_for_category(category):
    if category == "portrait":
        return _load_asset("brand_header.png"), None
    return _load_asset("brand_header_short.png"), _load_asset("brand_footer.png")


def convert(src_path, out_path, duration=12.0, fps=30, music=None):
    """Render one reel. If `music` is a path to an audio file, it becomes
    the reel's soundtrack: looped if shorter than the clip, trimmed to the
    clip length, with a 1-second fade-out at the end. With no music the
    reel gets a silent stereo track (some platforms reject video with no
    audio stream at all)."""
    photo, category = analyze(src_path)
    header_img, footer_img = _assets_for_category(category)

    if music:
        audio_inputs = ["-stream_loop", "-1", "-i", music]
        audio_filter = ["-af", f"afade=t=out:st={max(duration - 1.0, 0)}:d=1"]
    else:
        audio_inputs = ["-f", "lavfi",
                        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        audio_filter = []

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{CANVAS_W}x{CANVAS_H}",
        "-r", str(fps), "-i", "-",
        *audio_inputs,
        "-map", "0:v", "-map", "1:a",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "medium", "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        *audio_filter,
        "-shortest",
        "-use_editlist", "0",
        "-movflags", "+faststart",
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in render_frames(photo, category, duration, fps, header_img, footer_img):
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    finally:
        stderr = proc.stderr.read()
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"ffmpeg failed on {src_path}:\n{stderr[-2000:].decode(errors='replace')}")

    return category


MUSIC_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}


def pick_music(music_arg, key):
    """Resolve --music: a file is used as-is; a directory means 'pick one
    track per photo' -- stable for a given photo name (so re-runs give the
    same result) but varied across photos."""
    if not music_arg:
        return None
    if os.path.isfile(music_arg):
        return music_arg
    tracks = sorted(
        f for f in os.listdir(music_arg)
        if os.path.splitext(f)[1].lower() in MUSIC_EXTS
    )
    if not tracks:
        return None
    idx = sum(ord(c) for c in key) % len(tracks)   # stable per photo name
    return os.path.join(music_arg, tracks[idx])


def batch(input_dir, output_dir, duration, fps, music=None):
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
            category = convert(src, out, duration, fps, music=pick_music(music, f))
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
    ap.add_argument("--music", default=None,
                    help="audio file to use as the soundtrack, or a folder "
                         "of tracks to pick from (one per photo)")
    args = ap.parse_args()

    if args.batch:
        batch(args.input, args.output, args.duration, args.fps, music=args.music)
    else:
        music = pick_music(args.music, os.path.basename(args.input))
        category = convert(args.input, args.output, args.duration, args.fps, music=music)
        print(f"Done: {args.output} ({category} treatment)")


if __name__ == "__main__":
    main()
