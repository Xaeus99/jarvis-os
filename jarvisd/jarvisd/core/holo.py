"""v5.6 hologram workshop: geometry + scenes (pure Python/numpy, no Qt).

A scene is a list of parts built from parametric primitives (sphere, capsule, box, cylinder, cone, torus, lathe,
extrude), each with a position, rotation, scale, colour, optional label and a simple animation (spin, orbit, pulse),
plus 3D polylines (coastlines, orbits) and glow points. Presets: earth (+moon), armour suit (voice-editable), mannequin
(skeleton / organs / muscles), solar system, DNA, atom, car, rocket. Free-form scenes come from the local coder
model as JSON and are validated strictly here: no code ever runs, at most 400 primitives, numbers clamped."""
from __future__ import annotations
import json, math, os, re
import numpy as np

MAX_PRIMS = 400
SHAPES = ("sphere", "capsule", "box", "cylinder", "cone", "torus", "lathe", "extrude")
ANIMS = ("spin", "orbit", "pulse", "bob", None)

COLORS = {
    "purple": "#a855f7", "violet": "#8b5cf6", "gold": "#f5c542", "yellow": "#facc15", "red": "#ef4444", "crimson": "#b91c3c",
    "blue": "#3b82f6", "cyan": "#22d3ee", "teal": "#14b8a6", "green": "#22c55e", "lime": "#a3e635", "orange": "#f97316",
    "pink": "#ec4899", "white": "#f1f5f9", "silver": "#cbd5e1", "grey": "#64748b", "gray": "#64748b", "black": "#1e1e24",
    "brown": "#92603b", "wood": "#d4a373", "tan": "#d6b98c", "navy": "#1e3a8a", "magenta": "#d946ef", "chrome": "#e2e8f0",
}

def color(c, default="#22d3ee"):
    """'#a855f7' / 'purple' / [r,g,b] -> (r, g, b) ints; anything else -> default."""
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        try:
            return tuple(int(max(0, min(255, float(v) * (255 if all(float(x) <= 1 for x in c[:3]) else 1)))) for v in c[:3])
        except Exception:
            return color(default)
    s = str(c or "").strip().lower()
    s = COLORS.get(s, s)
    m = re.fullmatch(r"#?([0-9a-f]{6})", s)
    if not m:
        return color(default) if c != default else (34, 211, 238)
    h = m.group(1)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

# ---------------------------------------------------------------- primitives -> (verts (n,3), faces [tuple idx])
def _grid_faces(rows, cols, wrap=True):
    F = []
    for i in range(rows - 1):
        for j in range(cols if wrap else cols - 1):
            j2 = (j + 1) % cols
            F.append((i * cols + j, i * cols + j2, (i + 1) * cols + j2, (i + 1) * cols + j))
    return F

def lathe(profile, seg=16):
    """profile [(radius, y), ...] spun around the Y axis."""
    prof = [(max(0.0, float(r)), float(y)) for r, y in profile][:40]
    if len(prof) < 2:
        prof = [(0.5, -0.5), (0.5, 0.5)]
    V = []
    for r, y in prof:
        for j in range(seg):
            a = 2 * math.pi * j / seg
            V.append((r * math.cos(a), y, r * math.sin(a)))
    return np.array(V, dtype=np.float32), _grid_faces(len(prof), seg)

def sphere(seg=18, rings=10):
    prof = [(math.sin(math.pi * i / rings), -math.cos(math.pi * i / rings)) for i in range(rings + 1)]
    V, F = lathe([(r * 0.5, y * 0.5) for r, y in prof], seg)
    return V, F

def capsule(seg=14, rings=4, length=1.0):
    """Unit-width capsule along Y: total height 1 + length scaled later."""
    prof = []
    for i in range(rings + 1):
        a = -math.pi / 2 + (math.pi / 2) * i / rings
        prof.append((0.5 * math.cos(a), -0.5 * length + 0.5 * math.sin(a)))
    for i in range(rings + 1):
        a = (math.pi / 2) * i / rings
        prof.append((0.5 * math.cos(a), 0.5 * length + 0.5 * math.sin(a)))
    V, F = lathe(prof, seg)
    V[:, 1] /= (1 + length)
    return V, F

def cylinder(seg=16, top=1.0):
    return lathe([(0.0, -0.5), (0.5, -0.5), (0.5 * top, 0.5), (0.0, 0.5)], seg)

def cone(seg=16):
    return lathe([(0.0, -0.5), (0.5, -0.5), (0.0, 0.5)], seg)

def torus(seg=20, tube=8, r=0.4, t=0.1):
    V = []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        for j in range(tube):
            b = 2 * math.pi * j / tube
            V.append(((r + t * math.cos(b)) * math.cos(a), t * math.sin(b), (r + t * math.cos(b)) * math.sin(a)))
    F = []
    for i in range(seg):
        for j in range(tube):
            i2, j2 = (i + 1) % seg, (j + 1) % tube
            F.append((i * tube + j, i2 * tube + j, i2 * tube + j2, i * tube + j2))
    return np.array(V, dtype=np.float32), F

def box():
    V = np.array([(x, y, z) for x in (-.5, .5) for y in (-.5, .5) for z in (-.5, .5)], dtype=np.float32)
    F = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return V, F

def extrude(poly, depth=1.0):
    """poly [(x, y), ...] (a flat outline) pushed out along Z by depth."""
    pts = [(float(x), float(y)) for x, y in poly][:40]
    if len(pts) < 3:
        pts = [(-.5, -.5), (.5, -.5), (0, .5)]
    n = len(pts)
    V = [(x, y, -depth / 2) for x, y in pts] + [(x, y, depth / 2) for x, y in pts]
    F = [tuple(range(n)), tuple(range(2 * n - 1, n - 1, -1))]
    for i in range(n):
        j = (i + 1) % n
        F.append((i, j, n + j, n + i))
    return np.array(V, dtype=np.float32), F

def rot_matrix(rx, ry, rz):
    rx, ry, rz = (math.radians(float(a)) for a in (rx, ry, rz))
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]); Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return (Rz @ Ry @ Rx).astype(np.float32)

def _vec(v, n=3, default=0.0, lo=-50.0, hi=50.0):
    if isinstance(v, (int, float)):
        v = [v] * n
    if not isinstance(v, (list, tuple)):
        v = [default] * n
    out = []
    for i in range(n):
        try:
            x = float(v[i]) if i < len(v) else default
        except Exception:
            x = default
        if not math.isfinite(x):
            x = default
        out.append(max(lo, min(hi, x)))
    return out

def make_mesh(part, detail=1.0):
    """part dict -> (V world (n,3) float32, faces). Low detail for big free-form scenes."""
    sh = part.get("shape", "box")
    seg = max(6, int(16 * detail)); rings = max(4, int(10 * detail))
    if sh == "sphere":
        V, F = sphere(seg + 2, rings)
    elif sh == "capsule":
        V, F = capsule(max(6, int(14 * detail)), 3)
    elif sh == "cylinder":
        V, F = cylinder(seg, float(part.get("top", 1.0)))
    elif sh == "cone":
        V, F = cone(seg)
    elif sh == "torus":
        th = max(0.02, min(0.45, float(part.get("thickness", 0.1))))
        V, F = torus(max(8, int(22 * detail)), max(5, int(8 * detail)), 0.5 - th, th)
    elif sh == "lathe":
        V, F = lathe(part.get("profile") or [(0.5, -0.5), (0.5, 0.5)], seg)
    elif sh == "extrude":
        V, F = extrude(part.get("outline") or [(-.5, -.5), (.5, -.5), (0, .5)], 1.0)
    else:
        V, F = box()
    S = np.array(_vec(part.get("scale", part.get("size", 1.0)), default=1.0, lo=-40, hi=40), dtype=np.float32)
    S = np.where(np.abs(S) < 0.001, 0.001, S).astype(np.float32)
    R = rot_matrix(*_vec(part.get("rot", 0), lo=-720, hi=720))
    P = np.array(_vec(part.get("pos", 0), lo=-60, hi=60), dtype=np.float32)
    return (V * S) @ R.T + P, F

# ---------------------------------------------------------------- scene helpers
def P(shape, pos=(0, 0, 0), scale=(1, 1, 1), color="#22d3ee", rot=(0, 0, 0), label=None, alpha=0.35, glow=False, **kw):
    d = dict(shape=shape, pos=list(pos), scale=list(scale) if isinstance(scale, (list, tuple)) else [scale] * 3,
             color=color, rot=list(rot), alpha=alpha)
    if label:
        d["label"] = label
    if glow:
        d["glow"] = True
    d.update(kw)
    return d

def sphere_line(lonlat, r=1.0, tilt=23.4):
    pts = []
    for lon, lat in lonlat:
        lo, la = math.radians(lon), math.radians(lat)
        pts.append((r * math.cos(la) * math.cos(lo), r * math.sin(la), -r * math.cos(la) * math.sin(lo)))
    return pts

def circle_line(radius, n=64, y=0.0, center=(0, 0, 0)):
    return [(center[0] + radius * math.cos(2 * math.pi * i / n), center[1] + y, center[2] + radius * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

def _coastlines():
    try:
        with open(os.path.join(ASSETS, "coastlines_110m.json"), encoding="utf-8") as f:
            return json.load(f)["lines"]
    except Exception:
        return []

# ---------------------------------------------------------------- presets
def earth(moon=False, **_):
    tilt = 23.4
    parts = [P("sphere", (0, 0, 0), 2.0, "#1d4ed8", label="Earth", alpha=0.30, spin=8, detail=1.4, tilt=tilt, group="earth")]
    lines = []
    for ln in _coastlines():
        if len(ln) >= 2:
            lines.append(dict(points=sphere_line(ln, 1.006), color="#7ee787", width=1.4, spin=8, tilt=tilt, group="earth"))
    for lat in (-60, -30, 0, 30, 60):
        lines.append(dict(points=sphere_line([(lo, lat) for lo in range(-180, 181, 10)], 1.003), color="#60a5fa", width=0.6, alpha=0.35, spin=8, tilt=tilt, group="earth"))
    for lon in range(-180, 180, 30):
        lines.append(dict(points=sphere_line([(lon, la) for la in range(-90, 91, 10)], 1.003), color="#60a5fa", width=0.6, alpha=0.35, spin=8, tilt=tilt, group="earth"))
    glows = [dict(pos=(0, 0, 0), radius=1.18, color="#60a5fa", kind="atmosphere")]
    labels = [dict(pos=(0, 1.25, 0), text="North Pole")]
    if moon:
        parts.append(P("sphere", (3.2, 0.2, 0), 0.54, "#cbd5e1", label="Moon", alpha=0.45, orbit=6))
        lines.append(dict(points=circle_line(3.2, 96, 0.2), color="#94a3b8", width=0.7, alpha=0.4))
    return dict(title="Earth" + (" and Moon" if moon else ""), parts=parts, lines=lines, glows=glows, labels=labels,
                camera=dict(dist=5.2 if not moon else 8.0, pitch=-12))

SUIT_DEFAULT = dict(primary="crimson", secondary="gold", shoulders=1.0, wings=False, stealth=False)

def suit(primary="crimson", secondary="gold", shoulders=1.0, wings=False, stealth=False, **_):
    """An original armoured suit, built from primitives (no logos)."""
    a, b = (primary, secondary) if not stealth else ("#2a2d36", "#3a3f4b")
    glowc = "#7dd3fc" if not stealth else "#22d3ee"
    al = 0.42 if not stealth else 0.28
    sh = max(0.6, min(1.8, float(shoulders)))
    p = []
    # head
    p += [P("sphere", (0, 3.05, 0), (0.62, 0.74, 0.66), a, label="Helmet", alpha=al),
          P("box", (0, 3.0, 0.27), (0.46, 0.42, 0.14), b, alpha=al + 0.1),
          P("box", (-0.11, 3.1, 0.35), (0.14, 0.04, 0.02), glowc, glow=True, alpha=0.9),
          P("box", (0.11, 3.1, 0.35), (0.14, 0.04, 0.02), glowc, glow=True, alpha=0.9),
          P("cylinder", (0, 2.62, 0), (0.32, 0.22, 0.32), b, alpha=al)]
    # torso
    p += [P("lathe", (0, 1.95, 0), (1, 1, 0.62), a, profile=[(0.42, -0.55), (0.55, -0.1), (0.62, 0.3), (0.5, 0.55), (0.25, 0.62)], label="Chest plate", alpha=al),
          P("torus", (0, 2.12, 0.36), (0.26, 0.26, 0.26), b, rot=(90, 0, 0), thickness=0.12, alpha=0.8),
          P("sphere", (0, 2.12, 0.38), (0.16, 0.16, 0.08), glowc, glow=True, label="Arc reactor", alpha=0.95, pulse=1.4)]
    for i in range(3):
        p.append(P("box", (0, 1.30 - i * 0.17, 0.02), (0.62 - i * 0.04, 0.14, 0.42), b if i % 2 == 0 else a, alpha=al))
    p += [P("lathe", (0, 0.74, 0), (1, 1, 0.75), a, profile=[(0.30, -0.25), (0.42, 0.0), (0.36, 0.22)], alpha=al)]
    # shoulders + arms
    for s in (-1, 1):
        x = 0.78 * s
        p += [P("sphere", (x + 0.06 * s * (sh - 1), 2.42, 0), (0.42 * sh, 0.34 * sh, 0.42 * sh), b, label="Shoulder" if s > 0 else None, alpha=al),
              P("capsule", (x * 1.08, 1.86, 0), (0.26, 0.78, 0.26), a, alpha=al),
              P("sphere", (x * 1.1, 1.45, 0), 0.2, b, alpha=al),
              P("cylinder", (x * 1.12, 1.07, 0.05), (0.26, 0.7, 0.26), a, top=1.25, rot=(-8, 0, 0), alpha=al),
              P("box", (x * 1.13, 0.62, 0.1), (0.2, 0.22, 0.14), b, alpha=al),
              P("sphere", (x * 1.13, 0.58, 0.19), (0.1, 0.1, 0.04), glowc, glow=True, label="Repulsor" if s < 0 else None, alpha=0.95, pulse=2.0)]
        # legs
        p += [P("capsule", (0.24 * s, 0.05, 0), (0.32, 0.95, 0.34), a, alpha=al),
              P("sphere", (0.24 * s, -0.48, 0.04), 0.2, b, alpha=al),
              P("cylinder", (0.25 * s, -1.02, 0), (0.28, 0.95, 0.3), a, top=1.15, alpha=al),
              P("box", (0.25 * s, -1.56, 0.1), (0.3, 0.18, 0.5), b, label="Boot thruster" if s > 0 else None, alpha=al),
              P("sphere", (0.25 * s, -1.68, 0.05), (0.14, 0.05, 0.14), glowc, glow=True, alpha=0.9, pulse=3.0)]
        if wings:
            p.append(P("extrude", (0.62 * s, 2.0, -0.42), (1.0 * s, 1.0, 0.04), b,
                       outline=[(0, 0.3), (1.6, 0.9), (2.2, 0.6), (1.7, 0.1), (2.0, -0.4), (1.2, -0.3), (0.2, -0.3)],
                       label="Wing" if s > 0 else None, alpha=0.32))
    # back thrusters
    for s in (-1, 1):
        p.append(P("cylinder", (0.22 * s, 1.95, -0.45), (0.16, 0.5, 0.16), b, alpha=al))
    return dict(title="Armour suit" + (" (stealth)" if stealth else ""), parts=p, lines=[], glows=[],
                labels=[], camera=dict(dist=7.5, pitch=-6, target=(0, 0.75, 0)), params=dict(primary=primary, secondary=secondary,
                shoulders=sh, wings=bool(wings), stealth=bool(stealth)))

def mannequin(skeleton=False, organs=False, muscles=False, **_):
    """An artist's wooden mannequin (clothed-figure style, never realistic), with educational layers."""
    inside = skeleton or organs or muscles
    wood = "#d4a373"; al = 0.16 if inside else 0.40
    p = [P("sphere", (0, 3.05, 0), (0.5, 0.62, 0.52), wood, label="Head", alpha=al),
         P("cylinder", (0, 2.62, 0), (0.17, 0.25, 0.17), wood, alpha=al),
         P("sphere", (0, 2.05, 0), (0.95, 1.05, 0.5), wood, label="Chest", alpha=al),
         P("sphere", (0, 1.38, 0), (0.55, 0.32, 0.36), wood, alpha=al),
         P("sphere", (0, 0.85, 0), (0.82, 0.55, 0.45), wood, label="Hips", alpha=al)]
    for s in (-1, 1):
        p += [P("sphere", (0.55 * s, 2.45, 0), 0.22, wood, alpha=al),
              P("capsule", (0.62 * s, 1.95, 0), (0.22, 0.85, 0.22), wood, alpha=al),
              P("sphere", (0.64 * s, 1.5, 0), 0.15, wood, alpha=al, label="Elbow joint" if s > 0 else None),
              P("capsule", (0.66 * s, 1.08, 0), (0.18, 0.78, 0.18), wood, alpha=al),
              P("sphere", (0.67 * s, 0.65, 0), (0.17, 0.24, 0.1), wood, alpha=al),
              P("sphere", (0.3 * s, 0.6, 0), 0.2, wood, alpha=al),
              P("capsule", (0.3 * s, 0.0, 0), (0.3, 1.1, 0.3), wood, alpha=al),
              P("sphere", (0.3 * s, -0.6, 0), 0.18, wood, alpha=al, label="Knee joint" if s < 0 else None),
              P("capsule", (0.3 * s, -1.15, 0), (0.24, 1.0, 0.24), wood, alpha=al),
              P("box", (0.3 * s, -1.72, 0.12), (0.22, 0.12, 0.45), wood, alpha=al)]
    if skeleton:
        bone = "#f8fafc"
        p += [P("sphere", (0, 3.1, 0), (0.38, 0.44, 0.4), bone, label=None if organs else "Skull", alpha=0.5)]
        for i in range(12):
            p.append(P("sphere", (0, 2.55 - i * 0.14, -0.15), 0.07, bone, alpha=0.6, label="Spine" if i == 4 else None))
        for i in range(6):
            p.append(P("torus", (0, 2.4 - i * 0.12, -0.02), (0.82 - abs(i - 2) * 0.06, 0.6, 0.42), bone, thickness=0.04, alpha=0.5, label="Ribs" if i == 1 else None))
        p.append(P("torus", (0, 0.9, 0), (0.7, 0.5, 0.4), bone, thickness=0.12, alpha=0.5, label="Pelvis"))
        for s in (-1, 1):
            p += [P("cylinder", (0.62 * s, 1.95, 0), (0.06, 0.85, 0.06), bone, alpha=0.7),
                  P("cylinder", (0.66 * s, 1.08, 0), (0.05, 0.75, 0.05), bone, alpha=0.7),
                  P("cylinder", (0.3 * s, 0.0, 0), (0.08, 1.1, 0.08), bone, alpha=0.7, label="Femur" if s > 0 else None),
                  P("cylinder", (0.3 * s, -1.15, 0), (0.07, 1.0, 0.07), bone, alpha=0.7)]
    if organs:
        p += [P("sphere", (0, 3.12, 0), (0.34, 0.3, 0.36), "#f9a8d4", label="Brain", alpha=0.7),
              P("sphere", (-0.33, 2.15, 0), (0.32, 0.55, 0.28), "#fda4af", label="Lungs", alpha=0.55),
              P("sphere", (0.33, 2.15, 0), (0.32, 0.55, 0.28), "#fda4af", alpha=0.55),
              P("sphere", (0.08, 2.0, 0.12), (0.2, 0.24, 0.18), "#ef4444", label="Heart", alpha=0.85, pulse=1.2),
              P("sphere", (-0.22, 1.5, 0.05), (0.42, 0.22, 0.25), "#b45309", label="Liver", alpha=0.6),
              P("sphere", (0.22, 1.42, 0.08), (0.26, 0.2, 0.2), "#fbbf24", label="Stomach", alpha=0.6),
              P("torus", (0, 1.05, 0.05), (0.5, 0.5, 0.35), "#fca5a5", thickness=0.14, label="Intestines", alpha=0.5)]
    if muscles:
        mc = "#f43f5e"
        p += [P("sphere", (-0.3, 2.25, 0.2), (0.45, 0.32, 0.18), mc, label="Pectorals", alpha=0.55),
              P("sphere", (0.3, 2.25, 0.2), (0.45, 0.32, 0.18), mc, alpha=0.55)]
        for i in range(3):
            for s in (-1, 1):
                p.append(P("box", (0.12 * s, 1.75 - i * 0.2, 0.22), (0.2, 0.16, 0.08), mc, alpha=0.55, label="Abs" if (i == 1 and s > 0) else None))
        for s in (-1, 1):
            p += [P("sphere", (0.62 * s, 1.98, 0.08), (0.2, 0.4, 0.2), mc, label="Biceps" if s > 0 else None, alpha=0.55),
                  P("sphere", (0.3 * s, 0.1, 0.1), (0.3, 0.6, 0.26), mc, label="Quadriceps" if s < 0 else None, alpha=0.55),
                  P("sphere", (0.3 * s, -1.0, -0.08), (0.2, 0.42, 0.2), mc, label="Calf" if s > 0 else None, alpha=0.55),
                  P("sphere", (0.55 * s, 2.45, 0.02), (0.3, 0.24, 0.28), mc, label="Deltoid" if s < 0 else None, alpha=0.55)]
    layers = [n for n, on in (("skeleton", skeleton), ("organs", organs), ("muscles", muscles)) if on]
    return dict(title="Mannequin" + (" \u00b7 " + ", ".join(layers) if layers else ""), parts=p, lines=[], glows=[], labels=[],
                camera=dict(dist=7.6, pitch=-5, target=(0, 0.7, 0)), params=dict(skeleton=skeleton, organs=organs, muscles=muscles))

PLANETS = [("Mercury", 0.12, "#a8a29e", 1.6, 47), ("Venus", 0.2, "#fcd34d", 2.2, 35), ("Earth", 0.21, "#3b82f6", 2.9, 30),
           ("Mars", 0.16, "#ef4444", 3.6, 24), ("Jupiter", 0.5, "#f59e0b", 4.8, 13), ("Saturn", 0.42, "#fde68a", 6.0, 10),
           ("Uranus", 0.3, "#67e8f9", 7.0, 7), ("Neptune", 0.29, "#6366f1", 7.9, 5)]

def solar(**_):
    p = [P("sphere", (0, 0, 0), 1.1, "#fbbf24", label="Sun", alpha=0.7, glow=True, spin=5)]
    lines = []
    for i, (n, r, c, d, sp) in enumerate(PLANETS):
        a = i * 0.8
        p.append(P("sphere", (d * math.cos(a), 0, d * math.sin(a)), r * 2, c, label=n, alpha=0.6, orbit=sp * 0.6, detail=0.7))
        lines.append(dict(points=circle_line(d, 96), color="#94a3b8", width=0.6, alpha=0.35))
        if n == "Saturn":
            p.append(P("torus", (d * math.cos(a), 0, d * math.sin(a)), (1.4, 1.4, 1.4), "#fde68a", thickness=0.08, rot=(20, 0, 0), alpha=0.4, orbit=sp * 0.6))
    return dict(title="Solar system (sizes and distances not to scale)", parts=p, lines=lines, glows=[dict(pos=(0, 0, 0), radius=1.4, color="#fbbf24")],
                labels=[], camera=dict(dist=15, pitch=-28))

def dna(**_):
    p = []; turns = 24
    for i in range(turns):
        y = -3 + i * 0.26; a = i * 0.55
        x1, z1, x2, z2 = math.cos(a), math.sin(a), -math.cos(a), -math.sin(a)
        p += [P("sphere", (x1, y, z1), 0.22, "#a855f7", alpha=0.75), P("sphere", (x2, y, z2), 0.22, "#22d3ee", alpha=0.75)]
        ang = -math.degrees(a)
        p.append(P("cylinder", (0, y, 0), (0.07, 2.0, 0.07), ["#f472b6", "#facc15", "#4ade80", "#60a5fa"][i % 4], rot=(0, ang, 90), alpha=0.6))
    return dict(title="DNA double helix", parts=p, lines=[], glows=[], labels=[dict(pos=(1.3, 2.9, 0), text="Sugar-phosphate backbone"),
                dict(pos=(0, -3.3, 0), text="Base pairs")], camera=dict(dist=9, pitch=-10), spin_all=20)

def atom(**_):
    p = [P("sphere", (0.12 * math.cos(i * 2.1), 0.12 * math.sin(i * 1.3), 0.12 * math.sin(i * 2.1)), 0.3, "#ef4444" if i % 2 else "#94a3b8",
           alpha=0.8, label="Nucleus" if i == 0 else None) for i in range(8)]
    lines = []
    for k, rot in enumerate(((0, 0, 0), (60, 0, 0), (-60, 0, 0))):
        R = rot_matrix(*rot)
        pts = [tuple(R @ np.array(q)) for q in circle_line(2.2, 96)]
        lines.append(dict(points=pts, color="#22d3ee", width=1.0, alpha=0.6))
        e = tuple(R @ np.array((2.2, 0, 0)))
        p.append(P("sphere", e, 0.22, "#22d3ee", glow=True, alpha=0.95, orbit=90 + 20 * k, orbit_axis=list(R @ np.array((0, 1, 0))), label="Electron" if k == 0 else None))
    return dict(title="Atom (Bohr model)", parts=p, lines=lines, glows=[], labels=[], camera=dict(dist=7, pitch=-15))

def car(**_):
    c = "#a855f7"
    p = [P("box", (0, 0.45, 0), (4.2, 0.55, 1.8), c, label="Body", alpha=0.4),
         P("lathe", (0.2, 0.95, 0), (2.4, 1, 1.5), "#94a3b8", profile=[(0.5, -0.25), (0.42, 0.1), (0.25, 0.3)], label="Cabin", alpha=0.35, rot=(0, 0, 0)),
         P("box", (2.15, 0.5, 0), (0.1, 0.3, 1.6), "#f1f5f9", alpha=0.6, label="Headlights", glow=True)]
    for x in (-1.35, 1.35):
        for z in (-0.92, 0.92):
            p += [P("torus", (x, 0.2, z), (0.85, 0.85, 0.85), "#1e1e24", thickness=0.18, rot=(90, 0, 0), alpha=0.7, label="Wheel" if (x > 0 and z > 0) else None),
                  P("cylinder", (x, 0.2, z), (0.45, 0.2, 0.45), "#cbd5e1", rot=(90, 0, 0), alpha=0.5)]
    return dict(title="Sports car", parts=p, lines=[], glows=[], labels=[], camera=dict(dist=8, pitch=-18))

def rocket(**_):
    p = [P("lathe", (0, 1.2, 0), (1, 1, 1), "#e2e8f0", profile=[(0.0, -2.0), (0.55, -2.0), (0.6, -1.0), (0.6, 1.6), (0.45, 2.3), (0.0, 3.0)], label="Fuel tank", alpha=0.4),
         P("cone", (0, 4.6, 0), (1.0, 1.6, 1.0), "#a855f7", label="Payload fairing", alpha=0.5),
         P("cylinder", (0, -1.05, 0), (0.7, 0.5, 0.7), "#475569", top=0.7, label="Engine", alpha=0.6),
         P("cone", (0, -1.9, 0), (0.6, 1.4, 0.6), "#f97316", rot=(180, 0, 0), glow=True, alpha=0.7, label="Exhaust", pulse=6)]
    for k in range(4):
        a = k * 90
        p.append(P("extrude", (0.55 * math.cos(math.radians(a)), -0.4, 0.55 * math.sin(math.radians(a))), (1, 1, 0.06), "#a855f7",
                   outline=[(0, 0), (0.7, -0.6), (0.7, -1.0), (0, -0.4), (0, 0.7)], rot=(0, -a, 0), alpha=0.45, label="Fin" if k == 0 else None))
    return dict(title="Rocket", parts=p, lines=[], glows=[dict(pos=(0, -2.6, 0), radius=0.8, color="#f97316")], labels=[],
                camera=dict(dist=12, pitch=-8, target=(0, 1.2, 0)))

PRESETS = {"earth": earth, "suit": suit, "mannequin": mannequin, "solar": solar, "dna": dna, "atom": atom, "car": car, "rocket": rocket}
PRESET_WORDS = [
    ("earth", r"\b(?:earth|globe|world|planet earth|our planet|moon)\b"),
    ("suit", r"\b(?:suit|armou?r|iron ?man|mark \w+|exo ?suit|mech suit|power armou?r)\b"),
    ("mannequin", r"\b(?:human|body|mannequin|person|man|woman|skeleton|anatomy|organs?|muscles?|figure)\b"),
    ("solar", r"\b(?:solar system|planets|the sun|orbits?)\b"),
    ("dna", r"\b(?:dna|helix|genes?|genetic)\b"),
    ("atom", r"\b(?:atoms?|electrons?|nucleus|protons?|neutrons?)\b"),
    ("car", r"\b(?:car|truck|vehicle|lamborghini|ferrari|tesla|race ?car|motorcycle|bike|motorbike)\b"),
    ("rocket", r"\b(?:rocket|spaceship|space ?ship|shuttle|starship|missile)\b"),
]

def closest_preset(text):
    t = (text or "").lower()
    for name, rx in PRESET_WORDS:
        if re.search(rx, t):
            return name
    return None

# ---------------------------------------------------------------- free-form: the local model's JSON, validated strictly
SPEC_SYSTEM = (
    "You design 3D hologram models from simple shapes. Reply with ONLY JSON, no other text, in this format: "
    '{"title": "short name", "parts": [{"shape": "sphere|capsule|box|cylinder|cone|torus|lathe|extrude", '
    '"pos": [x, y, z], "scale": [sx, sy, sz], "rot": [degX, degY, degZ], "color": "#rrggbb", "label": "optional short label", '
    '"glow": false, "spin": 0}]} . Units: the whole object fits in about 6 units, y is up, centred at 0. '
    "lathe parts may add \"profile\": [[radius, y], ...]; extrude parts may add \"outline\": [[x, y], ...]; torus may add "
    '"thickness": 0.1. Use 10 to 80 parts. Make it look good and recognisable. Never people\'s faces, nothing violent or adult.')

def _clean_label(s):
    s = re.sub(r"[^\w\s\-',.()]", "", str(s or ""))[:32].strip()
    return s or None

def validate_spec(obj, max_prims=MAX_PRIMS):
    """-> (scene, problems). Strict: only known keys/shapes, numbers clamped, at most max_prims parts, no code."""
    problems = []
    if isinstance(obj, str):
        t = re.sub(r"<think>.*?</think>", "", obj, flags=re.S)
        t = re.sub(r"```(?:json)?", "", t)
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            return None, ["no JSON"]
        raw = m.group(0)
        obj = None
        for cand in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
            try:
                obj = json.loads(cand); break
            except Exception:
                continue
        if obj is None:
            return None, ["bad JSON"]
    if not isinstance(obj, dict) or not isinstance(obj.get("parts"), list):
        return None, ["no parts list"]
    parts = []
    for raw in obj["parts"]:
        if len(parts) >= max_prims:
            problems.append(f"clamped to {max_prims} parts"); break
        if not isinstance(raw, dict):
            continue
        shp = str(raw.get("shape", "")).lower().strip()
        if shp not in SHAPES:
            shp = {"ball": "sphere", "cube": "box", "pill": "capsule", "ring": "torus", "donut": "torus", "tube": "cylinder", "prism": "extrude"}.get(shp)
            if not shp:
                continue
        p = dict(shape=shp, pos=_vec(raw.get("pos", 0), lo=-20, hi=20), scale=_vec(raw.get("scale", raw.get("size", 1)), default=1.0, lo=0.01, hi=20),
                 rot=_vec(raw.get("rot", 0), lo=-360, hi=360), color="#%02x%02x%02x" % color(raw.get("color")),
                 alpha=max(0.1, min(0.95, float(raw.get("alpha", 0.45)) if isinstance(raw.get("alpha", 0.45), (int, float)) else 0.45)))
        lab = _clean_label(raw.get("label")) if raw.get("label") else None
        if lab:
            p["label"] = lab
        if raw.get("glow") is True:
            p["glow"] = True
        for k in ("spin", "orbit", "pulse"):
            v = raw.get(k)
            if isinstance(v, (int, float)) and math.isfinite(v) and v:
                p[k] = max(-180.0, min(180.0, float(v)))
        if shp == "torus" and isinstance(raw.get("thickness"), (int, float)):
            p["thickness"] = max(0.02, min(0.45, float(raw["thickness"])))
        if shp == "lathe" and isinstance(raw.get("profile"), list):
            prof = [_vec(q, 2, lo=-10, hi=10) for q in raw["profile"][:40] if isinstance(q, (list, tuple))]
            if len(prof) >= 2:
                p["profile"] = prof
        if shp == "extrude" and isinstance(raw.get("outline"), list):
            out = [_vec(q, 2, lo=-10, hi=10) for q in raw["outline"][:40] if isinstance(q, (list, tuple))]
            if len(out) >= 3:
                p["outline"] = out
        parts.append(p)
    if not parts:
        return None, problems + ["no usable parts"]
    title = _clean_label(obj.get("title")) or "Hologram"
    # fit to view: centre and scale to about 6 units
    allv = np.array([q["pos"] for q in parts], dtype=np.float32)
    span = float(np.max(np.abs(allv - allv.mean(axis=0)))) if len(parts) > 1 else 1.0
    return dict(title=title, parts=parts, lines=[], glows=[], labels=[], camera=dict(dist=max(6.0, min(30.0, span * 3.2 + 4)), pitch=-14,
                target=tuple(float(x) for x in allv.mean(axis=0))), freeform=True), problems

def check_text(text):
    """The image blocklists (adult, gore, hate, real people) apply to holograms too. -> (ok, kind)."""
    try:
        from ..safety import check_prompt
        ok, kind = check_prompt(text)
    except Exception:
        return False, "checks_unavailable"
    if ok and re.search(r"\b(?:naked|nude|undress\w*|without clothes|no clothes|sexy|lingerie|bikini|underwear|genitals?|"
                        r"penis|vagina|breasts?|butt|gun|rifle|bomb|weapon of|gore|guts|corpse|dead body)\b", (text or "").lower()):
        ok, kind = False, "adult" if not re.search(r"gun|rifle|bomb|weapon|gore|guts|corpse|dead", text.lower()) else "gore"
    return ok, kind

def build(name, **params):
    fn = PRESETS.get(name)
    if fn is None:
        raise KeyError(name)
    sc = fn(**params)
    sc["preset"] = name
    sc.setdefault("params", {}).update({k: v for k, v in params.items() if k not in sc.get("params", {})})
    return sc

def to_obj(scene, path):
    """Export every part as a mesh (Wavefront .obj + .mtl) for Blender. -> (obj path, mtl path)."""
    base = os.path.splitext(path)[0]
    mtl = base + ".mtl"
    vi = 1
    out = [f"# J.A.R.V.I.S. hologram: {scene.get('title', 'model')}", f"mtllib {os.path.basename(mtl)}"]
    mats = {}
    for i, part in enumerate(scene.get("parts", [])):
        V, F = make_mesh(part, float(part.get("detail", 1.0)))
        c = color(part.get("color"))
        mname = "m_%02x%02x%02x" % c
        mats[mname] = c
        out.append(f"o part{i}_{part.get('shape')}" + (f"_{re.sub(r'[^A-Za-z0-9]', '_', part['label'])}" if part.get("label") else ""))
        out.append(f"usemtl {mname}")
        out += [f"v {x:.4f} {y:.4f} {z:.4f}" for x, y, z in V.tolist()]
        out += ["f " + " ".join(str(vi + k) for k in f) for f in F]
        vi += len(V)
    with open(base + ".obj", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    with open(mtl, "w", encoding="utf-8") as f:
        for n, c in mats.items():
            f.write(f"newmtl {n}\nKd {c[0] / 255:.3f} {c[1] / 255:.3f} {c[2] / 255:.3f}\n\n")
    return base + ".obj", mtl
