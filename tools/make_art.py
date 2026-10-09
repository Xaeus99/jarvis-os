#!/usr/bin/env python3
"""Generate JARVIS OS artwork (wallpaper, GRUB background, menu highlight) in the Vibranium palette.
Pure PIL so it builds anywhere (CI or this box). Usage: make_art.py OUT_DIR [W H]"""
import math, os, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.environ.get("JARVIS_FONTS", os.path.join(HERE, "..", "image", "system_files", "usr", "share", "fonts", "jarvis"))
MAIN, LIGHT, DEEP, ACCENT = (176, 96, 255), (236, 214, 255), (84, 28, 156), (255, 92, 214)
BG0, BG1 = (9, 4, 19), (26, 11, 47)

def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)

def background(W, H):
    img = Image.new("RGB", (W, H), BG0)
    px = img.load()
    cx, cy = W * 0.5, H * 0.46
    maxd = math.hypot(W, H) * 0.6
    for y in range(H):
        for x in range(0, W):
            d = math.hypot(x - cx, (y - cy) * 1.15) / maxd
            t = max(0.0, 1.0 - d) ** 2.2
            px[x, y] = tuple(int(BG0[i] + (BG1[i] - BG0[i]) * t * 1.6) for i in range(3))
    return img

def grid(img, step=48, alpha=18):
    W, H = img.size
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    for x in range(0, W, step): d.line([(x, 0), (x, H)], fill=MAIN + (alpha,), width=1)
    for y in range(0, H, step): d.line([(0, y), (W, y)], fill=MAIN + (alpha,), width=1)
    img.paste(Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB"))

def reactor(img, cx, cy, r):
    """Arc-reactor style ring stack with glow."""
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0)); g = ImageDraw.Draw(glow)
    for k, (rr, w, col, a) in enumerate([(r, max(2, r // 28), MAIN, 255), (r * 0.82, max(2, r // 40), LIGHT, 200),
                                          (r * 0.62, max(3, r // 18), MAIN, 230), (r * 0.30, max(2, r // 30), ACCENT, 230)]):
        g.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=col + (a,), width=int(w))
    # segmented outer arcs
    for i in range(12):
        a0 = i * 30 + 4
        g.arc([cx - r * 1.12, cy - r * 1.12, cx + r * 1.12, cy + r * 1.12], a0, a0 + 20, fill=MAIN + (220,), width=max(3, r // 22))
    for i in range(60):
        a = math.radians(i * 6)
        r1, r2 = r * 0.9, r * (0.96 if i % 5 else 1.0)
        g.line([(cx + r1 * math.cos(a), cy + r1 * math.sin(a)), (cx + r2 * math.cos(a), cy + r2 * math.sin(a))], fill=LIGHT + (150,), width=2)
    core = r * 0.18
    g.ellipse([cx - core, cy - core, cx + core, cy + core], fill=LIGHT + (255,))
    blur = glow.filter(ImageFilter.GaussianBlur(max(4, r // 10)))
    base = img.convert("RGBA")
    base = Image.alpha_composite(base, blur); base = Image.alpha_composite(base, blur)
    base = Image.alpha_composite(base, glow)
    img.paste(base.convert("RGB"))

def wallpaper(W, H, title=True):
    img = background(W, H); grid(img)
    r = int(min(W, H) * 0.17)
    reactor(img, W * 0.5, H * 0.42, r)
    if title:
        d = ImageDraw.Draw(img)
        f = font("Orbitron-Bold.ttf", int(H * 0.055)); s = "J.A.R.V.I.S."
        tw = d.textlength(s, font=f); d.text(((W - tw) / 2, H * 0.42 + r * 1.35), s, font=f, fill=LIGHT)
        f2 = font("Rajdhani-SemiBold.ttf", int(H * 0.024)); s2 = "OPERATING SYSTEM  //  VIBRANIUM"
        tw = d.textlength(s2, font=f2); d.text(((W - tw) / 2, H * 0.42 + r * 1.35 + H * 0.075), s2, font=f2, fill=MAIN)
    return img

def grub_background(W, H):
    img = background(W, H); grid(img, 64, 14)
    r = int(H * 0.13); reactor(img, W * 0.5, H * 0.2, r)
    return img

def grub_select(w=56, h=56):
    """9-slice pieces for the highlighted menu entry: select_c/n/s/e/w/ne/nw/se/sw.png"""
    pieces = {}
    fill = (60, 22, 110, 235); edge = MAIN + (255,)
    for k in ["c", "n", "s", "e", "w", "ne", "nw", "se", "sw"]:
        im = Image.new("RGBA", (8, 8), fill); d = ImageDraw.Draw(im)
        if "n" in k: d.line([(0, 0), (7, 0)], fill=edge, width=2)
        if "s" in k: d.line([(0, 7), (7, 7)], fill=edge, width=2)
        if "w" in k: d.line([(0, 0), (0, 7)], fill=ACCENT + (255,), width=3)
        if "e" in k: d.line([(7, 0), (7, 7)], fill=edge, width=1)
        pieces[k] = im
    return pieces

if __name__ == "__main__":
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (2560, 1440)
    wallpaper(W, H).save(os.path.join(out, f"wallpaper_{W}x{H}.png"))
    grub_background(1920, 1080).save(os.path.join(out, "grub_background.png"))
    for k, im in grub_select().items(): im.save(os.path.join(out, f"select_{k}.png"))
    print("art written to", out)
