#!/usr/bin/env python3
"""JARVIS OS boot splash (Plymouth, two-step module) artwork in the Vibranium palette. Pure PIL.
Usage: make_plymouth_art.py [OUT_DIR]   (default: image/system_files/usr/share/plymouth/themes/jarvis)
watermark.png   the JARVIS mark + name, centred above the spinner
throbber-NNNN   a spinning arc-reactor ring (loops while the system starts)
animation-NNNN  the same ring settling (the end animation, used on shutdown/updates)
entry.png, bullet.png, lock.png  the disk-password box (only shown if a disk is encrypted)"""
import math, os, sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "..", "image", "system_files", "usr", "share", "fonts", "jarvis")
MAIN, LIGHT, DEEP = (176, 96, 255), (236, 214, 255), (84, 28, 156)
S = 4  # supersampling

def font(name, size):
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)

def watermark(W=520, H=300):
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    cx, cy, r = W * S // 2, 96 * S, 74 * S
    for rr, w, c in ((r, 6, MAIN), (r - 16 * S, 3, LIGHT), (r - 30 * S, 9, MAIN)):
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=c + (255,), width=w * S)
    for k in range(12):
        a = math.radians(k * 30 + 15)
        x1, y1 = cx + math.cos(a) * (r - 46 * S), cy + math.sin(a) * (r - 46 * S)
        x2, y2 = cx + math.cos(a) * (r - 34 * S), cy + math.sin(a) * (r - 34 * S)
        d.line([(x1, y1), (x2, y2)], fill=LIGHT + (255,), width=4 * S)
    d.ellipse([cx - 13 * S, cy - 13 * S, cx + 13 * S, cy + 13 * S], fill=LIGHT + (255,))
    f = font("Orbitron-Bold.ttf", 46 * S); t = "J.A.R.V.I.S."
    tw = d.textlength(t, font=f); d.text((cx - tw / 2, 186 * S), t, font=f, fill=(255, 255, 255, 255))
    f2 = font("Rajdhani-SemiBold.ttf", 22 * S); t2 = "O P E R A T I N G    S Y S T E M"
    tw2 = d.textlength(t2, font=f2); d.text((cx - tw2 / 2, 250 * S), t2, font=f2, fill=LIGHT + (255,))
    return img.resize((W, H), Image.LANCZOS)

def ring(phase, sweep=100, W=64):
    img = Image.new("RGBA", (W * S, W * S), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    m = 4 * S; box = [m, m, W * S - m, W * S - m]
    d.ellipse(box, outline=DEEP + (255,), width=3 * S)
    start = phase - 90
    for k in range(10):                                # a bright head fading into a tail
        a0 = start + sweep * k / 10; a1 = start + sweep * (k + 1) / 10
        al = int(60 + 195 * (k + 1) / 10)
        d.arc(box, a0, a1 + 1, fill=(MAIN if k < 8 else LIGHT) + (al,), width=5 * S)
    return img.resize((W, W), Image.LANCZOS)

def entry(W=300, H=34):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=6, fill=(22, 10, 44, 255), outline=MAIN + (255,), width=2)
    return img

def bullet(W=12):
    img = Image.new("RGBA", (W * S, W * S), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.ellipse([S, S, W * S - S, W * S - S], fill=LIGHT + (255,)); return img.resize((W, W), Image.LANCZOS)

def lock(W=24, H=30):
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    d.arc([5 * S, 1 * S, 19 * S, 17 * S], 180, 360, fill=LIGHT + (255,), width=3 * S)
    d.line([(5 * S + S, 9 * S), (5 * S + S, 14 * S)], fill=LIGHT + (255,), width=3 * S)
    d.line([(19 * S - S, 9 * S), (19 * S - S, 14 * S)], fill=LIGHT + (255,), width=3 * S)
    d.rounded_rectangle([1 * S, 13 * S, 23 * S, 29 * S], radius=3 * S, fill=MAIN + (255,))
    return img.resize((W, H), Image.LANCZOS)

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "image", "system_files", "usr", "share", "plymouth", "themes", "jarvis")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        if f.endswith(".png"): os.remove(os.path.join(out, f))
    watermark().save(os.path.join(out, "watermark.png"), optimize=True)
    N = 36
    for i in range(N):
        ring(i * 360 / N).save(os.path.join(out, "throbber-%04d.png" % (i + 1)), optimize=True)
        ring(i * 360 / N, sweep=100 + 260 * (i + 1) / N).save(os.path.join(out, "animation-%04d.png" % (i + 1)), optimize=True)
    entry().save(os.path.join(out, "entry.png")); bullet().save(os.path.join(out, "bullet.png")); lock().save(os.path.join(out, "lock.png"))
    print(len(os.listdir(out)), "files in", out)
