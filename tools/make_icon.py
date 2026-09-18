#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Draw the Robot Arm Web Console icon (pure Pillow, no photo input).

Design: dark rounded square + flat-vector articulated arm in an amber/orange
gradient, a cyan rotation ring under the base and two cyan "signal" arcs at the
tool tip (the "web control" hint).  Rendered with 4x supersampling and exported
as a multi-size .ico.

Usage::

    python tools/make_icon.py                 # -> tools/robot_arm.ico
    python tools/make_icon.py --preview out.png
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------
BG_TOP = (18, 23, 31)
BG_BOT = (32, 41, 55)
EDGE = (255, 255, 255, 34)

ARM_TOP = (255, 206, 92)
ARM_BOT = (238, 108, 22)
GLOW = (249, 130, 24)

JOINT_FILL = (255, 246, 228)
JOINT_RING = (188, 82, 12)

BASE_TOP = (247, 158, 48)
BASE_BOT = (196, 78, 12)

CYAN = (86, 205, 250)

SS = 4  # supersampling factor

# --------------------------------------------------------------------------
# arm geometry, normalized 0..1 (y grows downwards)
# --------------------------------------------------------------------------
A = (0.280, 0.715)          # shoulder pivot
B = (0.520, 0.500)          # elbow pivot
C = (0.680, 0.350)          # wrist pivot

BASE_FOOT = (0.100, 0.840, 0.460, 0.905, 0.028)
BASE_COL = (0.212, 0.650, 0.348, 0.865, 0.045)

# 16 px variant: no base, one very bold link, big pivots
M_A = (0.245, 0.775)
M_C = (0.730, 0.315)


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _vgrad(size, c0, c1):
    img = Image.new("RGB", (1, size))
    for y in range(size):
        img.putpixel((0, y), _lerp(c0, c1, y / max(1, size - 1)))
    return img.resize((size, size), Image.NEAREST)


def _dist(p, q):
    return math.hypot(q[0] - p[0], q[1] - p[1])


def _capsule(draw, p, q, width, fill):
    """Thick line with round caps."""
    draw.line([p, q], fill=fill, width=int(round(width)))
    r = width / 2.0
    for cx, cy in (p, q):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)


def render(size: int, detail: bool = True) -> Image.Image:
    S = size * SS
    micro = size <= 16          # 16 px gets a simplified, bolder glyph
    A0 = M_A if micro else A
    B0 = B
    C0 = M_C if micro else C
    u = lambda v: v * S  # noqa: E731

    # ---- background: rounded square with a vertical gradient -------------
    bg_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(bg_mask).rounded_rectangle(
        [0, 0, S - 1, S - 1], radius=u(0.225), fill=255)

    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.paste(_vgrad(S, BG_TOP, BG_BOT).convert("RGBA"), (0, 0), bg_mask)

    # ---- warm floor glow behind the arm ---------------------------------
    glow_bg = Image.new("L", (S, S), 0)
    gd = ImageDraw.Draw(glow_bg)
    gd.ellipse([u(0.00), u(0.50), u(0.72), u(1.05)], fill=58)
    canvas.paste(Image.new("RGBA", (S, S), GLOW + (255,)),
                 (0, 0), glow_bg.filter(ImageFilter.GaussianBlur(u(0.20))))

    # ---- base: pedestal + column (slightly darker than the arm) ---------
    base = Image.new("L", (S, S), 0)
    bd = ImageDraw.Draw(base)
    if not micro:
        bd.rounded_rectangle([u(BASE_FOOT[0]), u(BASE_FOOT[1]),
                              u(BASE_FOOT[2]), u(BASE_FOOT[3])],
                             radius=u(BASE_FOOT[4]), fill=255)
        bd.rounded_rectangle([u(BASE_COL[0]), u(BASE_COL[1]),
                              u(BASE_COL[2]), u(BASE_COL[3])],
                             radius=u(BASE_COL[4]), fill=255)
        canvas.paste(_vgrad(S, BASE_TOP, BASE_BOT).convert("RGBA"), (0, 0), base)

    # ---- arm silhouette mask --------------------------------------------
    arm = Image.new("L", (S, S), 0)
    ad = ImageDraw.Draw(arm)
    lw = u(0.086 if detail else (0.112 if not micro else 0.175))
    if micro:
        # single bold link: the elbow is unreadable at 16 px
        _capsule(ad, (u(A0[0]), u(A0[1])), (u(C0[0]), u(C0[1])), lw, 255)
    else:
        _capsule(ad, (u(A0[0]), u(A0[1])), (u(B0[0]), u(B0[1])), lw, 255)
        _capsule(ad, (u(B0[0]), u(B0[1])), (u(C0[0]), u(C0[1])), lw * 0.84, 255)

    # gripper: short wrist stub + two prongs opening in a V
    dx, dy = C0[0] - B0[0], C0[1] - B0[1]
    n = math.hypot(dx, dy)
    dx, dy = dx / n, dy / n
    px, py = -dy, dx
    D = (C0[0] + 0.052 * dx, C0[1] + 0.052 * dy)
    _capsule(ad, (u(C[0]), u(C[1])), (u(D[0]), u(D[1])), lw * 0.74, 255)
    pw = lw * (0.34 if detail else 0.44)
    for s in (+1, -1):
        ang = math.radians(20.0 * s)
        ca, sa = math.cos(ang), math.sin(ang)
        fdx, fdy = dx * ca - dy * sa, dx * sa + dy * ca
        s0 = (D[0] + s * 0.042 * px, D[1] + s * 0.042 * py)
        s1 = (s0[0] + 0.090 * fdx, s0[1] + 0.090 * fdy)
        _capsule(ad, (u(s0[0]), u(s0[1])), (u(s1[0]), u(s1[1])), pw, 255)

    # ---- glow + gradient body -------------------------------------------
    halo = arm.filter(ImageFilter.GaussianBlur(u(0.050)))
    canvas.paste(Image.new("RGBA", (S, S), GLOW + (255,)),
                 (0, 0), halo.point(lambda v: int(v * 0.35)))
    canvas.paste(_vgrad(S, ARM_TOP, ARM_BOT).convert("RGBA"), (0, 0), arm)

    # ---- joints ----------------------------------------------------------
    jd = ImageDraw.Draw(canvas)
    for p, r in ((A0, 0.048), (B0, 0.042), (C0, 0.036)):
        if micro and p is B0:
            continue
        rr = u(r if detail else (r * 1.30 if not micro else r * 1.85))
        cx, cy = u(p[0]), u(p[1])
        jd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=JOINT_RING + (255,))
        ir = rr * 0.50
        jd.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], fill=JOINT_FILL + (255,))

    # ---- cyan accents ----------------------------------------------------
    if detail:
        layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)

        # turntable ring hugging the base plate
        ring = [u(A[0] - 0.170), u(0.858), u(A[0] + 0.170), u(0.938)]
        ld.arc(ring, start=0, end=360, fill=CYAN + (150,), width=int(u(0.015)))
        ld.arc(ring, start=12, end=168, fill=CYAN + (235,), width=int(u(0.022)))

        # signal arcs radiating from the tool tip
        for i, radius in enumerate((0.200, 0.272)):
            box = [u(C[0] - radius), u(C[1] - radius),
                   u(C[0] + radius), u(C[1] + radius)]
            alpha = 235 if i == 0 else 145
            ld.arc(box, start=-76, end=-14, fill=CYAN + (alpha,),
                   width=int(u(0.023 if i == 0 else 0.016)))
        canvas.alpha_composite(layer)

    # ---- rim highlight ---------------------------------------------------
    rd = ImageDraw.Draw(canvas)
    inset = u(0.012)
    rd.rounded_rectangle([inset, inset, S - 1 - inset, S - 1 - inset],
                         radius=u(0.215), outline=EDGE, width=max(1, int(u(0.006))))

    # clip everything back to the rounded square
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(canvas, (0, 0), bg_mask)
    return out.resize((size, size), Image.LANCZOS)


SIZES = (256, 128, 64, 48, 32, 24, 16)


def build_ico(path: Path) -> None:
    frames = {s: render(s, detail=s >= 48) for s in SIZES}
    base = frames[max(SIZES)]
    rest = [frames[s] for s in SIZES if s != max(SIZES)]
    base.save(path, format="ICO", append_images=rest,
              sizes=[(s, s) for s in SIZES])
    print("wrote %s (%d sizes)" % (path, len(SIZES)))


def main() -> int:
    ap = argparse.ArgumentParser(description="Draw the console icon")
    ap.add_argument("--out", default=str(Path(__file__).with_name("robot_arm.ico")))
    ap.add_argument("--preview", help="write a PNG preview instead of the .ico")
    args = ap.parse_args()

    if args.preview:
        board = Image.new("RGBA", (256 * 4 + 60, 300), (24, 26, 30, 255))
        x = 20
        for s in (256, 128, 64, 32):
            img = render(s, detail=s >= 32)
            board.alpha_composite(img, (x, 20 + (256 - s) // 2))
            x += s + 20
        board.save(args.preview)
        print("wrote preview %s" % args.preview)
        return 0

    build_ico(Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
