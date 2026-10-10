"""v5.6 thought cloud, 3D edition: JARVIS's brain as a slowly rotating 3D point cloud (QPainter only, no OpenGL needed).

What's new over the v5.5 flat brain:
  - real depth: every neuron dot is a 3D point; the cloud turns slowly (drag to spin it), with perspective parallax,
    depth fog (far dots are smaller and dimmer) and a soft bloom glow on every working region
  - neurons pulse softly; the whole brain "breathes" when idle
  - curved bezier signal paths with travelling light particles, only where data really flows (ears -> core -> race ->
    voice, image maker -> safety, core -> Tab link ...), straight from the brain bus events
  - a HUD ring around the core with live GPU VRAM and CPU, and an original arc reactor core (brain.arc_reactor)
Light on the PC: about 900 dots drawn in a few batched calls per frame, 30 fps cap, the timer stops when hidden or
minimised (BrainHost minimises it in games). Same interface as brainview.BrainCanvas (bus, fps, compact, step, layout_)."""
from __future__ import annotations
import math, random, time
from PySide6 import QtCore, QtGui, QtWidgets
from . import brainbus as bb

PURPLE = (176, 102, 255)
CORE_BLUE = (70, 160, 255)

# the real pipelines inside JARVIS (drawn as faint pathways; they light up when either end works)
PATHWAYS = [("ears", "core"), ("core", "race_a"), ("core", "race_b"), ("core", "race_c"), ("race_a", "voice"),
            ("race_b", "voice"), ("race_c", "voice"), ("core", "thinker"), ("thinker", "voice"), ("core", "eyes"),
            ("core", "image"), ("image", "safety"), ("core", "coder"), ("core", "tab"), ("core", "memory"),
            ("core", "motor"), ("memory", "race_b")]

def _c(rgb, a=255):
    return QtGui.QColor(max(0, min(255, int(rgb[0]))), max(0, min(255, int(rgb[1]))), max(0, min(255, int(rgb[2]))), max(0, min(255, int(a))))

def region_pos3(angle, radius, key):
    """A region's place inside the brain volume: the v5.5 2D layout plus depth, so the front/back regions separate."""
    a = math.radians(angle)
    h = (sum(map(ord, key)) % 7) / 6.0 - 0.5            # stable per-region depth
    return (math.cos(a) * radius * 1.05, math.sin(a) * radius * 0.78, h * 0.9 + 0.25 * math.sin(a * 2))

def bezier(p0, p1, p2, t):
    u = 1 - t
    return tuple(u * u * p0[i] + 2 * u * t * p1[i] + t * t * p2[i] for i in range(3))

class Flow:
    """Light particles running along a curved path from region a to region b (both 3D)."""
    __slots__ = ("a", "b", "col", "t", "speed", "n", "lift")
    def __init__(self, a, b, col, speed=0.03, n=5, lift=0.35):
        self.a, self.b, self.col, self.t, self.speed, self.n, self.lift = a, b, col, 0.0, speed, n, lift

class _Brain3D:
    """All the drawing; mixed into a plain QWidget (software) or a QOpenGLWidget (GPU-painted). See make_canvas()."""

    def _init3d(self, bus=None, fps=30, compact=False, seed=7, reactor=True, clock=None):
        self.bus = bus or bb.BUS
        self.compact = compact
        self.reactor = reactor
        self.fps = max(10, min(30 if not compact else 24, int(fps or 30)))     # 30 fps cap
        self.seed = seed
        self.clock = clock or time.monotonic
        self.t0 = self.clock()
        self.yaw = 0.35; self.pitch = -0.18; self.spin = 0.10          # radians, radians/second
        self.drag = None; self.drag_yaw = 0.0; self.moved = False
        self.layout_ = {}            # key -> dict(center=QPointF (projected, this frame), r=px, col)
        self.flows = []
        self.seen_ev = 0.0
        self.snap = None
        self.frames = 0; self._fps_t = time.monotonic(); self.real_fps = 0.0
        self._last_step = None
        self.bg = None
        self.selected = None
        self.build_model()
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.CoarseTimer)
        self.timer.timeout.connect(self.step)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setMinimumSize(320, 220)
        self.setMouseTracking(False)

    # ---- lifecycle ----
    def showEvent(self, e):
        self.timer.start(int(1000 / self.fps)); super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop(); super().hideEvent(e)

    def resizeEvent(self, e):
        self.bg = None; super().resizeEvent(e)

    # ---- the 3D model (built once) ----
    def build_model(self):
        rnd = random.Random(self.seed)
        self.centers3 = {"core": (0.0, 0.0, 0.0)}
        self.points = {}                 # key -> list of (x, y, z, phase)
        total_budget = 560 if self.compact else 1100
        want = {k: bb.neurons_for(p) for k, _l, _r, _m, p, *_ in bb.REGIONS}
        scale = min(1.0, total_budget * 0.75 / max(1, sum(want.values())))
        for key, label, role, model, params, col, ang, rad in bb.REGIONS:
            c = region_pos3(ang, rad, key)
            self.centers3[key] = c
            n = max(14, int(want[key] * scale))
            spread = 0.07 + 0.0009 * want[key]
            pts = []
            for _ in range(n):
                pts.append((c[0] + rnd.gauss(0, spread * 1.2), c[1] + rnd.gauss(0, spread), c[2] + rnd.gauss(0, spread),
                            rnd.uniform(0, math.tau)))
            self.points[key] = pts
        # the faint "cortex" shell: two hemispheres of dust
        shell = []
        for _ in range(int(total_budget * 0.25)):
            u = rnd.uniform(-1, 1); th = rnd.uniform(0, math.tau); r = 1.0 + rnd.gauss(0, 0.04)
            x = math.sqrt(1 - u * u) * math.cos(th); y = u; z = math.sqrt(1 - u * u) * math.sin(th)
            x *= 1.22; y *= 0.92; z *= 0.82
            x += 0.05 if x > 0 else -0.05                       # the gap between hemispheres
            shell.append((x * r, y * r, z * r, rnd.uniform(0, math.tau)))
        self.shell = shell
        self.stars = [(rnd.random(), rnd.random(), rnd.random()) for _ in range(90 if not self.compact else 40)]

    # ---- projection ----
    def now(self):
        return self.clock() - self.t0

    def view(self):
        w, h = max(1, self.width()), max(1, self.height())
        cx, cy = w / 2, h / 2 + (6 if self.compact else 16)
        f = min(w * 0.33, h * 0.50) * (0.92 if self.compact else 1.0)
        return cx, cy, f

    def project(self, p, cy_, sy, cp, sp, cx, cyy, f, breathe=1.0):
        x, y, z = p[0] * breathe, p[1] * breathe, p[2] * breathe
        x, z = x * cy_ + z * sy, -x * sy + z * cy_            # yaw
        y, z = y * cp - z * sp, y * sp + z * cp                # pitch
        d = 3.2
        s = d / (d + z)                                        # perspective: nearer is bigger
        return cx + x * f * s, cyy + y * f * s, z, s

    # ---- input: drag to spin, click a region to open its panel ----
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.drag = e.position(); self.drag_yaw = (self.yaw, self.pitch); self.moved = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.drag is not None:
            d = e.position() - self.drag
            if abs(d.x()) + abs(d.y()) > 4:
                self.moved = True
                self.yaw = self.drag_yaw[0] + d.x() * 0.01
                self.pitch = max(-1.0, min(1.0, self.drag_yaw[1] + d.y() * 0.006))
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.drag is not None and not self.moved:
            k = self.region_at(e.position())
            self.regionClicked.emit(k or "")
        self.drag = None
        super().mouseReleaseEvent(e)

    def region_at(self, pos):
        best, bd = None, 1e9
        for k, L in self.layout_.items():
            c = L["center"]; d = math.hypot(c.x() - pos.x(), c.y() - pos.y())
            if d < max(28.0, L["r"] * 1.4) and d < bd:
                best, bd = k, d
        return best

    # ---- animation step ----
    def step(self):
        now = self.clock()
        dt = 1.0 / self.fps if self._last_step is None else max(0.0, min(0.2, now - self._last_step))
        self._last_step = now
        snap = self.bus.snapshot()
        self.snap = snap
        if self.drag is None:
            busy = sum(1 for R in snap["regions"] if R["busy"])
            self.yaw += dt * (self.spin + 0.05 * min(busy, 3))     # spins a little faster while thinking
        cols = {R["key"]: R["color"] for R in snap["regions"]}
        for (t, kind, a, b) in snap["events"]:
            if t <= self.seen_ev:
                continue
            self.seen_ev = t
            if kind == "fire" and a in self.centers3 and b in self.centers3 and a != b:
                col = cols.get(a if a != "core" else b, (150, 170, 255))
                self.flows.append(Flow(a, b, col, speed=random.uniform(0.022, 0.034), n=6, lift=random.uniform(0.25, 0.5)))
            elif kind == "flash" and a in self.centers3:
                self.flows.append(Flow(a, "voice" if a.startswith("race") else "core", (255, 230, 140), 0.03, 8, 0.45))
        for R in snap["regions"]:                       # busy regions keep streaming data to and from the core
            if R["busy"] and random.random() < 0.10:
                a, b = (R["key"], "core") if random.random() < 0.5 else ("core", R["key"])
                self.flows.append(Flow(a, b, R["color"], random.uniform(0.02, 0.035), 4, random.uniform(0.2, 0.45)))
        for fl in self.flows:
            fl.t += fl.speed * (dt * 30)
        self.flows = [fl for fl in self.flows if fl.t < 1.35][-90:]
        self.frames += 1
        if now - self._fps_t >= 1.0:
            self.real_fps = self.frames / max(1e-6, now - self._fps_t); self.frames = 0; self._fps_t = now
        self.update()

    # ---- painting ----
    def paint_bg(self, w, h):
        pm = QtGui.QPixmap(w, h)
        p = QtGui.QPainter(pm)
        g = QtGui.QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        g.setColorAt(0, QtGui.QColor(16, 12, 34)); g.setColorAt(0.55, QtGui.QColor(7, 7, 18)); g.setColorAt(1, QtGui.QColor(2, 2, 6))
        p.fillRect(0, 0, w, h, g)
        p.setPen(QtCore.Qt.NoPen)
        for x, y, b in self.stars:
            p.setBrush(QtGui.QColor(200, 210, 255, int(30 + 90 * b)))
            r = 0.6 + 1.1 * b
            p.drawEllipse(QtCore.QPointF(x * w, y * h), r, r)
        p.end()
        return pm

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        if self.bg is None or self.bg.size() != QtCore.QSize(w, h):
            self.bg = self.paint_bg(w, h)
        snap = self.snap or self.bus.snapshot()
        self.paint_frame(snap, w, h)

    def paint_frame(self, snap, w, h, painter=None):
        p = painter or QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if self.bg is None or self.bg.size() != QtCore.QSize(w, h):
            self.bg = self.paint_bg(w, h)
        p.drawPixmap(0, 0, self.bg)
        t = self.now()
        cx, cy, f = self.view()
        cyw, syw, cpt, spt = math.cos(self.yaw), math.sin(self.yaw), math.cos(self.pitch), math.sin(self.pitch)
        regions = {R["key"]: R for R in snap["regions"]}
        busy_any = any(R["busy"] for R in regions.values())
        breathe = 1.0 + (0.012 if busy_any else 0.022) * math.sin(t * (1.6 if busy_any else 0.9))
        proj = lambda q: self.project(q, cyw, syw, cpt, spt, cx, cy, f, breathe)
        P = {k: proj(c) for k, c in self.centers3.items()}

        # 1) cortex shell dust (fogged by depth)
        p.setPen(QtCore.Qt.NoPen)
        bins = [[], [], []]
        for q in self.shell:
            x, y, z, s = proj(q)
            bins[0 if z > 0.35 else (1 if z > -0.35 else 2)].append(QtCore.QPointF(x, y))
        for i, pts in enumerate(bins):
            pen = QtGui.QPen(_c((150, 120, 255), (40, 70, 110)[i]), (1.3, 1.8, 2.4)[i] * (0.8 if self.compact else 1.0))
            pen.setCapStyle(QtCore.Qt.RoundCap); p.setPen(pen); p.drawPoints(pts)

        # 2) pathways (faint bezier wiring along the real pipelines)
        for a, b in PATHWAYS:
            if a not in P or b not in P:
                continue
            la = regions.get(a, {}).get("level", 0.5 if a == "core" else 0.1)
            lb = regions.get(b, {}).get("level", 0.5 if b == "core" else 0.1)
            lvl = max(la if a != "core" else 0, lb if b != "core" else 0)
            col = regions.get(b if b != "core" else a, {}).get("color", PURPLE)
            ctrl = self._ctrl(a, b, 0.3)
            pa, pc, pb = P[a], proj(ctrl), P[b]
            path = QtGui.QPainterPath(QtCore.QPointF(pa[0], pa[1]))
            path.quadTo(QtCore.QPointF(pc[0], pc[1]), QtCore.QPointF(pb[0], pb[1]))
            fog = 0.55 + 0.45 * (1 - max(-1, min(1, (pa[2] + pb[2]) / 2)))
            p.setPen(QtGui.QPen(_c(col, (14 + 120 * lvl) * fog), 0.8 + 1.6 * lvl))
            p.setBrush(QtCore.Qt.NoBrush); p.drawPath(path)

        # 3) regions, far to near: bloom glow + neuron dots that pulse
        order = sorted((k for k in regions if k in P), key=lambda k: -P[k][2])
        self.layout_ = {}
        for k in order:
            R = regions[k]; col = R["color"]; lvl = R["level"]; x, y, z, s = P[k]
            fog = max(0.25, min(1.0, 0.75 - z * 0.45))
            rad = (34 + 46 * lvl) * s * (0.7 if self.compact else 1.0)
            if R.get("flashing"):
                rad *= 1.35 + 0.15 * math.sin(t * 18)
            g = QtGui.QRadialGradient(x, y, rad * 1.8)
            g.setColorAt(0, _c(col, (40 + 150 * lvl) * fog)); g.setColorAt(0.35, _c(col, (14 + 60 * lvl) * fog)); g.setColorAt(1, _c(col, 0))
            p.setPen(QtCore.Qt.NoPen); p.setBrush(g); p.drawEllipse(QtCore.QPointF(x, y), rad * 1.8, rad * 1.8)
            # neuron dots in two pulse groups x depth, batched
            groups = {}
            for q in self.points[k]:
                px, py, pz, ps = proj(q)
                ph = 0 if math.sin(t * (3.0 if R["busy"] else 1.1) + q[3]) > 0 else 1
                groups.setdefault((ph, pz > z), []).append(QtCore.QPointF(px, py))
            for (ph, back), pts in groups.items():
                a = (70 + 185 * lvl) * fog * (1.0 if ph == 0 else 0.55) * (0.75 if back else 1.0)
                pen = QtGui.QPen(_c(col, a), (2.2 + 2.0 * lvl) * s * (0.8 if self.compact else 1.0) * (1.25 if ph == 0 else 1.0))
                pen.setCapStyle(QtCore.Qt.RoundCap); p.setPen(pen); p.drawPoints(pts)
            self.layout_[k] = dict(center=QtCore.QPointF(x, y), r=rad, col=col, z=z)

        # 4) signal flows: light particles riding curved paths
        for fl in self.flows:
            ctrl = self._ctrl(fl.a, fl.b, fl.lift)
            A, C, Bp = self.centers3[fl.a], ctrl, self.centers3[fl.b]
            for i in range(fl.n):
                tt = fl.t - i * 0.045
                if tt < 0 or tt > 1:
                    continue
                q = bezier(A, C, Bp, tt)
                x, y, z, s = proj(q)
                head = i == 0
                r = (4.2 if head else 3.0 - i * 0.35) * s * (0.75 if self.compact else 1.0)
                al = 255 * (1 - i / fl.n) * max(0.35, min(1, 0.8 - z * 0.4))
                g = QtGui.QRadialGradient(x, y, r * 2.6)
                g.setColorAt(0, _c((255, 255, 255) if head else fl.col, al)); g.setColorAt(0.4, _c(fl.col, al * 0.6)); g.setColorAt(1, _c(fl.col, 0))
                p.setPen(QtCore.Qt.NoPen); p.setBrush(g); p.drawEllipse(QtCore.QPointF(x, y), r * 2.6, r * 2.6)

        # 5) the core + HUD ring
        self.paint_core(p, snap, t, P["core"], busy_any)
        # 6) labels
        self.paint_labels(p, snap, regions, P, order)
        if not self.compact:
            self.paint_header(p, snap, w)
        if self.selected and self.selected in self.layout_:
            L = self.layout_[self.selected]
            p.setPen(QtGui.QPen(_c(PURPLE, 220), 1.6, QtCore.Qt.DashLine)); p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(L["center"], L["r"] * 1.2 + 6, L["r"] * 1.2 + 6)
        if painter is None:
            p.end()

    def _ctrl(self, a, b, lift):
        A, B = self.centers3[a], self.centers3[b]
        m = [(A[i] + B[i]) / 2 for i in range(3)]
        n = math.sqrt(m[0] ** 2 + m[1] ** 2 + m[2] ** 2) or 1.0
        if n < 0.05:
            return (m[0], m[1] - lift, m[2] + lift * 0.5)
        return (m[0] + m[0] / n * lift, m[1] + m[1] / n * lift - lift * 0.3, m[2] + m[2] / n * lift)

    def paint_core(self, p, snap, t, cpos, busy):
        x, y, z, s = cpos
        state = snap.get("state", "idle")
        base = (28 if self.compact else 46) * s
        pulse = 1.0 + (0.10 if busy or state in ("thinking", "speaking") else 0.04) * math.sin(t * (5 if busy else 1.4))
        g = QtGui.QRadialGradient(x, y, base * 3.4 * pulse)
        g.setColorAt(0, _c((220, 235, 255), 235)); g.setColorAt(0.18, _c(CORE_BLUE, 200)); g.setColorAt(0.45, _c(PURPLE, 70)); g.setColorAt(1, _c(PURPLE, 0))
        p.setPen(QtCore.Qt.NoPen); p.setBrush(g); p.drawEllipse(QtCore.QPointF(x, y), base * 3.4 * pulse, base * 3.4 * pulse)
        c = QtCore.QPointF(x, y)
        if self.reactor:
            # an arc reactor: bright centre, a segmented coil ring, a thin outer housing
            p.setBrush(_c((235, 245, 255), 250)); p.drawEllipse(c, base * 0.30, base * 0.30)
            p.setBrush(QtCore.Qt.NoBrush)
            p.setPen(QtGui.QPen(_c((150, 210, 255), 230), base * 0.10)); p.drawEllipse(c, base * 0.52, base * 0.52)
            seg = 10; rot = t * (40 if busy else 12)
            pen = QtGui.QPen(_c((120, 190, 255), 220), base * 0.22); pen.setCapStyle(QtCore.Qt.FlatCap); p.setPen(pen)
            rr = base * 0.82
            for i in range(seg):
                p.drawArc(QtCore.QRectF(x - rr, y - rr, rr * 2, rr * 2), int((rot + i * 36) * 16), int(24 * 16))
            p.setPen(QtGui.QPen(_c(PURPLE, 200), base * 0.06)); p.drawEllipse(c, base * 1.08, base * 1.08)
        else:
            p.setBrush(_c((210, 230, 255), 240)); p.drawEllipse(c, base * 0.55, base * 0.55)
            p.setBrush(QtCore.Qt.NoBrush); p.setPen(QtGui.QPen(_c(PURPLE, 210), 2.2)); p.drawEllipse(c, base * 1.05, base * 1.05)
        # HUD ring: VRAM (outer, purple) and CPU (inner, cyan), with rotating ticks
        R1 = base * 1.55; R2 = base * 1.35
        used, total = snap.get("vram_used"), snap.get("vram_total")
        vf = (used / total) if used and total else None
        cpu = snap.get("cpu")
        p.setPen(QtGui.QPen(_c((90, 90, 140), 70), 3)); p.drawEllipse(c, R1, R1)
        p.setPen(QtGui.QPen(_c((90, 90, 140), 60), 2)); p.drawEllipse(c, R2, R2)
        if vf is not None:
            p.setPen(QtGui.QPen(_c(PURPLE, 230), 3.2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawArc(QtCore.QRectF(x - R1, y - R1, R1 * 2, R1 * 2), 90 * 16, -int(360 * 16 * max(0.01, min(1, vf))))
        if cpu is not None:
            p.setPen(QtGui.QPen(_c((80, 220, 255), 220), 2.2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawArc(QtCore.QRectF(x - R2, y - R2, R2 * 2, R2 * 2), 90 * 16, -int(360 * 16 * max(0.01, min(1, cpu / 100.0))))
        p.setPen(QtGui.QPen(_c((180, 190, 255), 90), 1))
        R3 = base * 1.75
        for i in range(36):
            a = math.radians(i * 10 + t * 6)
            r0 = R3 if i % 3 else R3 - 4
            p.drawLine(QtCore.QPointF(x + math.cos(a) * r0, y + math.sin(a) * r0),
                       QtCore.QPointF(x + math.cos(a) * (R3 + 3), y + math.sin(a) * (R3 + 3)))
        if not self.compact:
            f = QtGui.QFont("Segoe UI", 8); f.setBold(True); p.setFont(f)
            bits = []
            if vf is not None:
                bits.append(f"VRAM {used / 2**30:.1f}/{total / 2**30:.0f} GB")
            if cpu is not None:
                bits.append(f"CPU {cpu:.0f}%")
            if bits:
                p.setPen(_c((200, 205, 255), 210))
                p.drawText(QtCore.QRectF(x - 120, y + R3 + 6, 240, 16), QtCore.Qt.AlignCenter, "  \u00b7  ".join(bits))
            p.setPen(_c((235, 240, 255), 230))
            p.drawText(QtCore.QRectF(x - 120, y + R3 + 22, 240, 16), QtCore.Qt.AlignCenter, state.upper())

    def paint_labels(self, p, snap, regions, P, order):
        fs = 7 if self.compact else 9
        f1 = QtGui.QFont("Segoe UI", fs); f1.setBold(True)
        f2 = QtGui.QFont("Segoe UI", max(6, fs - 2))
        w = self.width()
        for k in order:
            R = regions[k]; x, y, z, s = P[k]
            fog = max(0.35, min(1.0, 0.85 - z * 0.4))
            lvl = R["level"]
            dx = 1 if x >= w / 2 else -1
            lx = x + dx * (26 + 30 * s)
            ly = y - 18 * s
            p.setPen(QtGui.QPen(_c(R["color"], 120 * fog), 1))
            p.drawLine(QtCore.QPointF(x + dx * 10 * s, y - 4 * s), QtCore.QPointF(lx, ly + 6))
            title = R["label"] + ("  \u2605 WINNER" if R.get("flashing") else "")
            p.setFont(f1); p.setPen(_c((255, 255, 255), (150 + 105 * lvl) * fog))
            fm = QtGui.QFontMetrics(f1)
            tw = fm.horizontalAdvance(title)
            if dx > 0 and lx + tw > w - 8:            # keep labels on screen: flip to the left of the region
                dx = -1; lx = x - (26 + 30 * s)
            elif dx < 0 and lx - tw < 8:
                dx = 1; lx = x + (26 + 30 * s)
            tx = lx if dx > 0 else lx - tw
            p.drawText(QtCore.QPointF(tx, ly + 4), title)
            if not self.compact or R["busy"]:
                sub = f"{R['role']} \u00b7 {R['model']}" if not self.compact else R["role"]
                line2 = f"{bb.neuron_label(R['params'])} \u00b7 {R['stat']}" if R.get("params") else R["stat"]
                p.setFont(f2); fm2 = QtGui.QFontMetrics(f2)
                for i, txt in enumerate((sub, line2) if not self.compact else (line2,)):
                    tx2 = lx if dx > 0 else lx - fm2.horizontalAdvance(txt)
                    p.setPen(_c(R["color"], (110 + 120 * lvl) * fog))
                    p.drawText(QtCore.QPointF(tx2, ly + 18 + i * 13), txt)

    def paint_header(self, p, snap, w):
        f = QtGui.QFont("Segoe UI", 13); f.setBold(True); p.setFont(f)
        p.setPen(_c((235, 238, 255), 235))
        p.drawText(QtCore.QPointF(22, 34), "J.A.R.V.I.S.  THOUGHT CLOUD")
        f2 = QtGui.QFont("Segoe UI", 9); p.setFont(f2); p.setPen(_c(PURPLE, 230))
        firing = sum(1 for R in snap["regions"] if R["busy"])
        gpu = snap.get("gpu") or "GPU"
        p.drawText(QtCore.QPointF(22, 54), f"{firing} of {len(snap['regions'])} regions firing \u00b7 {gpu} \u00b7 drag to spin \u00b7 click a region for details")
        eta = getattr(self.bus, "update_eta", None)
        if eta:
            try:
                from .hud56 import countdown_text
                txt, left = countdown_text(eta)
                f3 = QtGui.QFont("Segoe UI", 10); f3.setBold(True); p.setFont(f3)
                s = f"NEXT UPDATE v{eta.get('version')} \u00b7 {eta.get('percent')}% \u00b7 {txt}"
                fm = QtGui.QFontMetrics(f3)
                p.setPen(_c((200, 160, 255), 235)); p.drawText(QtCore.QPointF(w - fm.horizontalAdvance(s) - 22, 34), s)
            except Exception:
                pass


class BrainCanvas3D(_Brain3D, QtWidgets.QWidget):
    """Software-painted (works everywhere, including the test box)."""
    regionClicked = QtCore.Signal(str)
    gl = False

    def __init__(self, bus=None, fps=30, compact=False, parent=None, seed=7, reactor=True, clock=None):
        QtWidgets.QWidget.__init__(self, parent)
        self._init3d(bus, fps, compact, seed, reactor, clock)


_GL_CLASS = None

def _gl_class():
    global _GL_CLASS
    if _GL_CLASS is None:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget

        class BrainCanvas3DGL(_Brain3D, QOpenGLWidget):
            """The same 3D scene, painted on the graphics card (QOpenGLWidget's GPU paint engine)."""
            regionClicked = QtCore.Signal(str)
            gl = True

            def __init__(self, bus=None, fps=30, compact=False, parent=None, seed=7, reactor=True, clock=None):
                QOpenGLWidget.__init__(self, parent)
                self._init3d(bus, fps, compact, seed, reactor, clock)
                self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, False)

            def paintEvent(self, e):
                QOpenGLWidget.paintEvent(self, e)

            def paintGL(self):
                w, h = self.width(), self.height()
                snap = self.snap or self.bus.snapshot()
                p = QtGui.QPainter(self)
                try:
                    self.paint_frame(snap, w, h, painter=p)
                finally:
                    p.end()
        _GL_CLASS = BrainCanvas3DGL
    return _GL_CLASS

def make_canvas(bus=None, fps=30, compact=False, parent=None, reactor=True, prefer_gl=True):
    """GPU-painted when OpenGL works on this PC, otherwise the software canvas (same picture)."""
    import os
    try:
        plat = QtGui.QGuiApplication.platformName()
    except Exception:
        plat = ""
    if prefer_gl and plat not in ("offscreen", "minimal", "vnc") and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        try:
            return _gl_class()(bus, fps=fps, compact=compact, parent=parent, reactor=reactor)
        except Exception:
            pass
    return BrainCanvas3D(bus, fps=fps, compact=compact, parent=parent, reactor=reactor)
