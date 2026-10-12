#!/usr/bin/env python3
"""Installer artwork for the JARVIS OS installer (Anaconda) in the Vibranium palette: the sidebar background, the
sidebar logo and the spoke top bar. Pure PIL. Usage: make_installer_art.py OUT_DIR
The PNGs are committed under image/installer/art/ and embedded into the kickstart by tools/sync_installer_guard.py;
image/installer/installer-branding.sh (kickstart %pre) puts them over the base's installer pictures."""
import math, os, sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "..", "image", "system_files", "usr", "share", "fonts", "jarvis")
MAIN, LIGHT, DEEP = (176, 96, 255), (236, 214, 255), (84, 28, 156)
BG0, BG1 = (9, 4, 19), (26, 11, 47)

def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)

def sidebar(W=240, H=1600):
    img = Image.new("RGB", (W, H), BG0); px = img.load()
    for y in range(H):
        t = max(0.0, 1.0 - y / 900.0) ** 1.6
        for x in range(W):
            px[x, y] = tuple(int(BG0[i] + (BG1[i] - BG0[i]) * t) for i in range(3))
    d = ImageDraw.Draw(img)
    for y in range(0, H, 40): d.line([(0, y), (W, y)], fill=(18, 9, 34))
    for x in range(0, W, 40): d.line([(x, 0), (x, H)], fill=(18, 9, 34))
    d.line([(W - 2, 0), (W - 2, H)], fill=DEEP, width=2)       # thin edge toward the page
    return img

def logo(W=170, H=170):
    S = 4; img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    cx, cy, r = W * S // 2, 62 * S, 46 * S
    for rr, w, c in ((r, 5, MAIN), (r - 11 * S, 3, LIGHT), (r - 20 * S, 7, MAIN)):
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=c + (255,), width=w * S)
    for k in range(12):                                         # arc-reactor segments
        a = math.radians(k * 30 + 15)
        x1, y1 = cx + math.cos(a) * (r - 30 * S), cy + math.sin(a) * (r - 30 * S)
        x2, y2 = cx + math.cos(a) * (r - 22 * S), cy + math.sin(a) * (r - 22 * S)
        d.line([(x1, y1), (x2, y2)], fill=LIGHT + (255,), width=3 * S)
    d.ellipse([cx - 9 * S, cy - 9 * S, cx + 9 * S, cy + 9 * S], fill=LIGHT + (255,))
    f = font("Orbitron-Bold.ttf", 25 * S)
    t = "JARVIS"; tw = d.textlength(t, font=f); d.text((cx - tw / 2, 118 * S), t, font=f, fill=(255, 255, 255, 255))
    f2 = font("Rajdhani-SemiBold.ttf", 15 * S)
    t2 = "O P E R A T I N G   S Y S T E M"; tw2 = d.textlength(t2, font=f2)
    while tw2 > W * S - 8 * S: f2 = font("Rajdhani-SemiBold.ttf", f2.size - S); tw2 = d.textlength(t2, font=f2)
    d.text((cx - tw2 / 2, 150 * S), t2, font=f2, fill=LIGHT + (255,))
    return img.resize((W, H), Image.LANCZOS)

def topbar(W=64, H=64):
    img = Image.new("RGB", (W, H), (22, 10, 44)); d = ImageDraw.Draw(img)
    d.line([(0, H - 1), (W, H - 1)], fill=DEEP)
    return img

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "image", "installer", "art")
    os.makedirs(out, exist_ok=True)
    sidebar().quantize(64).save(os.path.join(out, "sidebar-bg.png"), optimize=True)
    logo().save(os.path.join(out, "sidebar-logo.png"), optimize=True)
    topbar().save(os.path.join(out, "topbar-bg.png"), optimize=True)
    print(sorted(os.listdir(out)))
