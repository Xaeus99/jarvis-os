"""v5.5 thought cloud: JARVIS's live neural brain (PySide6, QPainter only, no OpenGL needed).

Glowing blue core with a purple ring in the middle; around it one cluster of neuron dots per real part of JARVIS
(brainbus.REGIONS). A region lights up while its part works, glows softly while its model is loaded and dims when idle;
signal streaks fly between regions and the core whenever work is handed over; the race winner flashes. Every label
shows live stats from the bus (model, VRAM from Ollama, tokens/s, or idle).

Light on the PC: the static wiring is painted once into a cached pixmap; each frame only draws glows, active wiring,
streaks and labels. 30 fps by default (brain.fps, max 60); the timer stops while the window is hidden or minimised.
BrainHost hides it during games and never takes focus from a game; the pop-up mode is click-through-free and
shows without activating."""
from __future__ import annotations
import math, random, time
from PySide6 import QtCore, QtGui, QtWidgets
from . import brainbus as bb

PURPLE = QtGui.QColor(176, 102, 255)
CORE_BLUE = QtGui.QColor(70, 160, 255)

def _c(rgb, a=255):
    return QtGui.QColor(rgb[0], rgb[1], rgb[2], max(0, min(255, int(a))))


class Streak:
    __slots__ = ("p0", "p1", "ctrl", "t", "speed", "col", "width")

    def __init__(self, p0, p1, col, speed, width=2.0, bend=0.25):
        self.p0, self.p1, self.col, self.speed, self.width = p0, p1, col, speed, width
        mx, my = (p0.x() + p1.x()) / 2, (p0.y() + p1.y()) / 2
        dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
        b = random.uniform(-bend, bend)
        self.ctrl = QtCore.QPointF(mx - dy * b, my + dx * b)
        self.t = 0.0

    def at(self, t):
        u = 1 - t
        return QtCore.QPointF(u * u * self.p0.x() + 2 * u * t * self.ctrl.x() + t * t * self.p1.x(),
                              u * u * self.p0.y() + 2 * u * t * self.ctrl.y() + t * t * self.p1.y())


class BrainCanvas(QtWidgets.QWidget):
    def __init__(self, bus=None, fps=30, compact=False, parent=None, seed=7):
        super().__init__(parent)
        self.bus = bus or bb.BUS
        self.compact = compact
        self.fps = max(10, min(60, int(fps or 30)))
        self.seed = seed
        self.layout_ = {}            # key -> dict(center, pts, edges, r)
        self.static = None
        self.streaks = []
        self.seen_ev = 0.0
        self.frames = 0; self._fps_t = time.monotonic(); self.real_fps = 0.0
        self.t0 = time.monotonic()
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.CoarseTimer)
        self.timer.timeout.connect(self.step)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setMinimumSize(320, 220)

    # ---- lifecycle: only animate while visible ----
    def showEvent(self, e):
        self.timer.start(int(1000 / self.fps)); super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop(); super().hideEvent(e)

    def resizeEvent(self, e):
        self.build(); super().resizeEvent(e)

    # ---- geometry ----
    def center(self):
        return QtCore.QPointF(self.width() / 2, self.height() / 2 + (8 if self.compact else 18))

    def region_center(self, angle, radius):
        c = self.center()
        rx = self.width() * (0.40 if self.compact else 0.40)
        ry = self.height() * (0.36 if self.compact else 0.37)
        a = math.radians(angle)
        return QtCore.QPointF(c.x() + math.cos(a) * rx * radius, c.y() + math.sin(a) * ry * radius)

    def build(self):
        rnd = random.Random(self.seed)
        w, h = max(1, self.width()), max(1, self.height())
        scale = min(w, h) / 900.0
        self.layout_ = {}
        for key, label, role, model, params, col, ang, rad in bb.REGIONS:
            c = self.region_center(ang, rad)
            n = bb.neurons_for(params)
            if self.compact:
                n = max(10, n // 2)
            spread = (40 + n * 1.3) * scale * (0.8 if self.compact else 1.0)
            pts = []
            for _ in range(n):
                r = abs(rnd.gauss(0, 0.55)) * spread
                a = rnd.uniform(0, math.tau)
                pts.append(QtCore.QPointF(c.x() + math.cos(a) * r * 1.25, c.y() + math.sin(a) * r))
            edges = set()
            for i, p in enumerate(pts):
                near = sorted(range(n), key=lambda j: (pts[j].x() - p.x()) ** 2 + (pts[j].y() - p.y()) ** 2)[1:4]
                for j in near:
                    edges.add((min(i, j), max(i, j)))
            self.layout_[key] = dict(center=c, pts=pts, edges=sorted(edges), r=spread, col=col)
        self.static = self.paint_static(w, h)

    def paint_static(self, w, h):
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pm = QtGui.QPixmap(int(w * dpr), int(h * dpr)); pm.setDevicePixelRatio(dpr)
        p = QtGui.QPainter(pm); p.setRenderHint(QtGui.QPainter.Antialiasing)
        g = QtGui.QRadialGradient(self.center(), max(w, h) * 0.7)
        g.setColorAt(0, QtGui.QColor(12, 14, 34)); g.setColorAt(0.6, QtGui.QColor(6, 7, 18)); g.setColorAt(1, QtGui.QColor(2, 2, 8))
        p.fillRect(0, 0, w, h, g)
        rnd = random.Random(self.seed + 1)              # faint star dust
        p.setPen(QtCore.Qt.NoPen)
        for _ in range(int(w * h / 2500)):
            p.setBrush(QtGui.QColor(120, 140, 255, rnd.randint(10, 45)))
            p.drawEllipse(QtCore.QPointF(rnd.uniform(0, w), rnd.uniform(0, h)), 0.9, 0.9)
        c = self.center()                                 # faint tracts from the core to every region
        for key, L in self.layout_.items():
            pen = QtGui.QPen(_c(L["col"], 26), 1.0); p.setPen(pen); p.setBrush(_c(L["col"], 12))   # soft tract beams
            path = QtGui.QPainterPath(c)
            m = L["center"]
            path.quadTo(QtCore.QPointF((c.x() + m.x()) / 2 + (m.y() - c.y()) * 0.15, (c.y() + m.y()) / 2 - (m.x() - c.x()) * 0.15), m)
            p.drawPath(path)
        for key, L in self.layout_.items():               # dim wiring + neurons
            p.setPen(QtGui.QPen(_c(L["col"], 34), 0.8))
            for i, j in L["edges"]:
                p.drawLine(L["pts"][i], L["pts"][j])
            p.setPen(QtCore.Qt.NoPen); p.setBrush(_c(L["col"], 70))
            for q in L["pts"]:
                p.drawEllipse(q, 1.6, 1.6)
        p.end()
        return pm

    # ---- animation ----
    def step(self):
        snap = self.bus.snapshot()
        self.snap = snap
        pos = {k: L["center"] for k, L in self.layout_.items()}
        pos["core"] = self.center()
        for (t, kind, a, b) in snap["events"]:
            if t <= self.seen_ev:
                continue
            self.seen_ev = t
            if kind == "fire" and a in pos and b in pos and a != b:
                col = self.layout_.get(a if a != "core" else b, {}).get("col", (150, 170, 255))
                for _ in range(3):
                    self.streaks.append(Streak(self._jit(pos[a]), self._jit(pos[b]), col, random.uniform(0.025, 0.05), 2.2))
        for R in snap["regions"]:                           # busy regions keep chattering with the core
            if R["busy"] and random.random() < 0.35 and R["key"] in pos:
                src, dst = (R["key"], "core") if random.random() < 0.5 else ("core", R["key"])
                self.streaks.append(Streak(self._jit(pos[src]), self._jit(pos[dst]), R["color"], random.uniform(0.02, 0.045), 1.6))
            elif R["loaded"] and random.random() < 0.01 and R["key"] in pos:
                self.streaks.append(Streak(self._jit(pos[R["key"]]), self._jit(pos["core"]), R["color"], 0.02, 1.0))
        for s in self.streaks:
            s.t += s.speed
        self.streaks = [s for s in self.streaks if s.t < 1.25][-160:]
        self.frames += 1
        now = time.monotonic()
        if now - self._fps_t >= 1.0:
            self.real_fps = self.frames / (now - self._fps_t); self.frames = 0; self._fps_t = now
        self.update()

    def _jit(self, p):
        return QtCore.QPointF(p.x() + random.uniform(-10, 10), p.y() + random.uniform(-8, 8))

    # ---- painting ----
    def paintEvent(self, e):
        if self.static is None or not self.layout_:
            self.build()
        snap = getattr(self, "snap", None) or self.bus.snapshot()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.drawPixmap(0, 0, self.static)
        t = time.monotonic() - self.t0
        p.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
        for R in snap["regions"]:
            L = self.layout_.get(R["key"])
            if not L:
                continue
            lv = R["level"] + (0.6 if R["flashing"] and int(t * 8) % 2 == 0 else 0)
            if lv < 0.06:
                continue
            g = QtGui.QRadialGradient(L["center"], L["r"] * 1.9)
            g.setColorAt(0, _c(R["color"], 90 * min(1.3, lv))); g.setColorAt(1, _c(R["color"], 0))
            p.setPen(QtCore.Qt.NoPen); p.setBrush(g)
            p.drawEllipse(L["center"], L["r"] * 1.9, L["r"] * 1.6)
            if lv > 0.2:
                flick = 0.75 + 0.25 * math.sin(t * 9 + hash(R["key"]) % 7) if R["busy"] else 1.0
                p.setPen(QtGui.QPen(_c(R["color"], 150 * min(1, lv) * flick), 1.0))
                for i, j in L["edges"]:
                    p.drawLine(L["pts"][i], L["pts"][j])
                p.setPen(QtCore.Qt.NoPen)
                for n, q in enumerate(L["pts"]):
                    on = R["busy"] and (math.sin(t * 6 + n * 1.7) > 0.3)
                    p.setBrush(QtGui.QColor(255, 255, 255, 220) if on else _c(R["color"], 200 * min(1, lv)))
                    p.drawEllipse(q, 2.6 if on else 2.0, 2.6 if on else 2.0)
        for s in self.streaks:                               # streaks: a bright head with a fading tail
            head = min(1.0, s.t); tail = max(0.0, s.t - 0.25)
            if head <= tail:
                continue
            steps = 6
            prev = s.at(tail)
            for k in range(1, steps + 1):
                u = tail + (head - tail) * k / steps
                q = s.at(u)
                p.setPen(QtGui.QPen(_c(s.col, 230 * k / steps), s.width * (0.4 + 0.6 * k / steps), QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
                p.drawLine(prev, q); prev = q
            if s.t <= 1.0:
                p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(255, 255, 255, 230)); p.drawEllipse(prev, 2.2, 2.2)
        self.paint_core(p, snap, t)
        p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
        self.paint_labels(p, snap)
        self.paint_header(p, snap)
        p.end()

    def paint_core(self, p, snap, t):
        c = self.center()
        busy = any(R["busy"] for R in snap["regions"])
        base = min(self.width(), self.height()) * (0.07 if self.compact else 0.075)
        pulse = 1 + (0.12 if busy else 0.05) * math.sin(t * (5 if busy else 2))
        r = base * pulse
        g = QtGui.QRadialGradient(c, r * 2.6)
        g.setColorAt(0, QtGui.QColor(230, 245, 255, 255)); g.setColorAt(0.18, QtGui.QColor(110, 190, 255, 230))
        g.setColorAt(0.45, QtGui.QColor(50, 110, 255, 110)); g.setColorAt(1, QtGui.QColor(30, 40, 160, 0))
        p.setPen(QtCore.Qt.NoPen); p.setBrush(g); p.drawEllipse(c, r * 2.6, r * 2.6)
        rnd = random.Random(int(t * 4))                      # the core's own tangle of fibres
        p.setPen(QtGui.QPen(QtGui.QColor(160, 210, 255, 70), 0.8))
        for _ in range(26):
            a1, a2 = rnd.uniform(0, math.tau), rnd.uniform(0, math.tau)
            r1, r2 = rnd.uniform(0.2, 1.3) * r, rnd.uniform(0.2, 1.3) * r
            p.drawLine(QtCore.QPointF(c.x() + math.cos(a1) * r1, c.y() + math.sin(a1) * r1),
                       QtCore.QPointF(c.x() + math.cos(a2) * r2, c.y() + math.sin(a2) * r2))
        ring = QtGui.QPen(QtGui.QColor(PURPLE.red(), PURPLE.green(), PURPLE.blue(), 200), 2.0)
        p.setPen(ring); p.setBrush(QtCore.Qt.NoBrush)
        span = 16 * (90 + 60 * math.sin(t * 1.3))
        rect = QtCore.QRectF(c.x() - r * 1.9, c.y() - r * 1.9, r * 3.8, r * 3.8)
        p.drawArc(rect, int(t * 16 * 80) % 5760, int(span)); p.drawArc(rect, int(t * 16 * 80 + 2880) % 5760, int(span))

    def paint_labels(self, p, snap):
        f1 = QtGui.QFont("Segoe UI", 8 if self.compact else 9); f1.setBold(True); f1.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.2)
        f2 = QtGui.QFont("Consolas", 7 if self.compact else 8)
        fm1, fm2 = QtGui.QFontMetrics(f1), QtGui.QFontMetrics(f2)
        c = self.center()
        placed = [QtCore.QRectF(c.x() - 60, c.y() - 60, 120, 120)]        # keep labels off the core
        for R in snap["regions"]:
            L = self.layout_.get(R["key"])
            if not L:
                continue
            if self.compact and not (R["busy"] or R["flashing"] or R["loaded"]):
                continue                                          # the corner pop-up only labels what's active
            lines2 = [f"{R['role']} \u00b7 {R['model']}", f"{bb.neuron_label(R['params'])} \u00b7 {R['stat']}"]
            if self.compact:
                lines2 = [f"{R['role']} \u00b7 {R['stat']}"]
            wbox = max([fm1.horizontalAdvance(R["label"])] + [fm2.horizontalAdvance(x) for x in lines2]) + 14
            hbox = fm1.height() + fm2.height() * len(lines2) + 8
            m = L["center"]
            dx = 1 if m.x() >= c.x() else -1
            x = m.x() + dx * (L["r"] * 0.9) + (0 if dx > 0 else -wbox)
            y = m.y() - L["r"] * 0.9 - hbox
            x = max(4, min(self.width() - wbox - 4, x)); y = max(30 if not self.compact else 4, min(self.height() - hbox - 4, y))
            rect = QtCore.QRectF(x, y, wbox, hbox)
            for _ in range(12):                                   # nudge up/down until it doesn't cover another label
                hit = next((r for r in placed if r.adjusted(-4, -4, 4, 4).intersects(rect)), None)
                if hit is None:
                    break
                up = rect.center().y() < hit.center().y()
                y = (hit.top() - hbox - 6) if up else (hit.bottom() + 6)
                if y < 30 or y > self.height() - hbox - 4:
                    y = hit.bottom() + 6 if up else hit.top() - hbox - 6
                rect = QtCore.QRectF(x, y, wbox, hbox)
            placed.append(rect)
            lit = R["busy"] or R["flashing"]
            a = 235 if lit else (170 if R["loaded"] else 110)
            p.setPen(QtGui.QPen(_c(R["color"], a * 0.8), 1.0))
            p.drawLine(QtCore.QPointF(x + (0 if dx > 0 else wbox), y + hbox), m)
            p.setBrush(QtGui.QColor(8, 10, 24, 200 if lit else 160))
            p.setPen(QtGui.QPen(_c(R["color"], a), 1.2 if lit else 0.8))
            p.drawRoundedRect(QtCore.QRectF(x, y, wbox, hbox), 4, 4)
            p.setFont(f1); p.setPen(_c(R["color"], 255 if lit else a))
            p.drawText(QtCore.QPointF(x + 7, y + 4 + fm1.ascent()), R["label"] + ("  \u2605 WINNER" if R["flashing"] else ""))
            p.setFont(f2); p.setPen(QtGui.QColor(225, 230, 255, a))
            yy = y + 4 + fm1.height()
            for s in lines2:
                p.drawText(QtCore.QPointF(x + 7, yy + fm2.ascent()), s); yy += fm2.height()

    def paint_header(self, p, snap):
        if self.compact:
            return
        f = QtGui.QFont("Segoe UI", 11); f.setBold(True); f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 3)
        p.setFont(f); p.setPen(PURPLE)
        p.drawText(QtCore.QPointF(16, 24), "J.A.R.V.I.S. \u00b7 THOUGHT CLOUD")
        f2 = QtGui.QFont("Consolas", 9); p.setFont(f2); p.setPen(QtGui.QColor(190, 200, 255, 220))
        busy = sum(1 for R in snap["regions"] if R["busy"]); loaded = sum(1 for R in snap["regions"] if R["loaded"])
        bits = [f"state {snap['state']}", f"{busy} firing", f"{loaded} loaded"]
        if snap.get("vram_total"):
            bits.append(f"VRAM {snap['vram_used'] / 2**30:.1f}/{snap['vram_total'] / 2**30:.1f} GB")
        bits.append(f"{self.real_fps:.0f} fps" if self.real_fps else f"{self.fps} fps cap")
        txt = "   ".join(bits)
        p.drawText(QtCore.QPointF(self.width() - QtGui.QFontMetrics(f2).horizontalAdvance(txt) - 16, 24), txt)
        p.setPen(QtGui.QColor(150, 160, 210, 150))
        p.drawText(QtCore.QPointF(16, self.height() - 12), "Live from this PC: regions light when that part of me is working. Esc to close.")


class BrainWindow(QtWidgets.QWidget):
    """The big window ("Jarvis, show your brain"), or the small corner pop-up while JARVIS thinks (popup=True)."""
    closed = QtCore.Signal()

    def __init__(self, bus=None, fps=30, popup=False, style="3d", reactor=True):
        flags = QtCore.Qt.Window
        if popup:
            flags = (QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint |
                     QtCore.Qt.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.popup = popup
        self.setWindowTitle("J.A.R.V.I.S. \u2014 Thought Cloud")
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, popup)
        if popup:
            self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.panel = None
        if style == "3d":
            try:                                   # v5.6: the 3D brain (GPU-painted when OpenGL works)
                from . import brain3d
                self.canvas = brain3d.make_canvas(bus, fps=fps, compact=popup, parent=self, reactor=reactor)
            except Exception:
                import logging; logging.getLogger("jarvis.brain").exception("3D brain failed; using the flat one")
                self.canvas = BrainCanvas(bus, fps=fps, compact=popup, parent=self)
        else:
            self.canvas = BrainCanvas(bus, fps=fps, compact=popup, parent=self)
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.canvas)
        if not popup and hasattr(self.canvas, "regionClicked"):
            self.panel = RegionPanel(bus or bb.BUS, parent=self.canvas)
            self.canvas.regionClicked.connect(self.open_region)
        self.resize(460, 300) if popup else self.resize(1180, 760)

    def open_region(self, key):
        """v5.6: click a region -> side panel with what that part is doing; click empty space -> close it."""
        if not self.panel:
            return
        if not key or key == getattr(self.panel, "key", None):
            self.panel.hide(); self.canvas.selected = None; return
        self.canvas.selected = key
        self.panel.open(key)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.panel:
            self.panel.place()

    def place_corner(self):
        scr = QtGui.QGuiApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry()
            self.move(g.right() - self.width() - 16, g.bottom() - self.height() - 16)

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    def changeEvent(self, e):
        if e.type() == QtCore.QEvent.WindowStateChange:
            if self.isMinimized():
                self.canvas.timer.stop()
            elif self.isVisible():
                self.canvas.timer.start(int(1000 / self.canvas.fps))
        super().changeEvent(e)

    def closeEvent(self, e):
        self.closed.emit(); super().closeEvent(e)


class BrainHost(QtCore.QObject):
    """Owns the brain windows on the Qt thread. show()/hide() are safe from any thread (queued signals).
    Game mode: every second, if a game is running, both windows hide; the pop-up never appears during a game."""
    _show = QtCore.Signal(bool)
    _hide = QtCore.Signal()
    _bus_ev = QtCore.Signal(str, str)

    def __init__(self, cfg=None, bus=None, is_gaming=lambda: False, poller=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg if cfg is not None else {}
        self.bus = bus or bb.BUS
        self.is_gaming = is_gaming
        self.poller = poller or bb.Poller(self.bus)
        self.win = None; self.pop = None
        self.hidden_for_game = False
        self.last_busy = 0.0
        self._show.connect(self._do_show); self._hide.connect(self._do_hide); self._bus_ev.connect(self._on_bus)
        self.bus.listeners.append(lambda kind, key: self._bus_ev.emit(kind, key))
        self.tick = QtCore.QTimer(self); self.tick.setInterval(1000); self.tick.timeout.connect(self._tick); self.tick.start()

    @property
    def fps(self):
        return max(10, min(60, int(self.cfg.get("fps", 30) or 30)))

    def popup_enabled(self):
        return bool(self.cfg.get("popup_while_thinking", False))

    def show(self, popup=False):
        self._show.emit(bool(popup))

    def hide(self):
        self._hide.emit()

    def is_open(self):
        return bool(self.win and self.win.isVisible())

    def _gaming(self):
        try:
            return bool(self.is_gaming())
        except Exception:
            return False

    def _do_show(self, popup):
        if self._gaming():
            return
        if popup:
            if self.is_open():
                return
            if self.pop is None:
                self.pop = BrainWindow(self.bus, fps=min(self.fps, 30), popup=True, style=self.cfg.get("style", "3d"),
                                       reactor=bool(self.cfg.get("arc_reactor", True)))
            self.pop.place_corner()
            self.pop.show()                      # WA_ShowWithoutActivating: focus stays where it was
        else:
            if self.pop:
                self.pop.hide()
            if self.win is None:
                self.win = BrainWindow(self.bus, fps=min(self.fps, 30), style=self.cfg.get("style", "3d"),
                                       reactor=bool(self.cfg.get("arc_reactor", True)))
                self.win.closed.connect(self._closed)
            self.win.showNormal(); self.win.raise_(); self.win.activateWindow()
        self.poller.start()

    def _closed(self):
        if not (self.pop and self.pop.isVisible()):
            self.poller.stop()

    def _do_hide(self):
        for w in (self.win, self.pop):
            if w:
                w.hide()
        self.poller.stop()

    def _on_bus(self, kind, key):
        if kind in ("begin", "think") and key in ("thinker", "race_a", "race_b", "race_c", "core", "coder", "image", "eyes"):
            self.last_busy = time.monotonic()
            if self.popup_enabled() and not self.is_open() and not self._gaming():
                self._do_show(True)

    def _tick(self):
        g = self._gaming()
        if g:
            if self.is_open() or (self.pop and self.pop.isVisible()):
                self.hidden_for_game = True
                if self.win and self.win.isVisible():
                    self.win.showMinimized()     # minimise, never close: it comes back when he wants it
                if self.pop:
                    self.pop.hide()
                self.poller.stop()
            return
        if self.pop and self.pop.isVisible():
            busy = any(g.busy for g in self.bus.r.values())
            if busy:
                self.last_busy = time.monotonic()
            elif time.monotonic() - self.last_busy > float(self.cfg.get("popup_seconds", 6)):
                self.pop.hide()
                if not self.is_open():
                    self.poller.stop()


class RegionPanel(QtWidgets.QFrame):
    """v5.6: the side panel for one brain region: what it's doing now, its last few inputs and outputs (short; memory
    and Tab text hidden when personal), model, VRAM and speed. Refreshes twice a second while open."""
    def __init__(self, bus, parent=None):
        super().__init__(parent)
        self.bus = bus; self.key = None
        self.setStyleSheet("QFrame{background:rgba(14,10,32,228);border:1px solid rgba(176,102,255,170);border-radius:12px;}"
                           "QLabel{color:#e6e6ff;background:transparent;border:none;font-family:'Segoe UI';}"
                           "QPushButton{color:#cfc0ff;background:transparent;border:none;font-size:16px;}")
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(16, 12, 16, 14); lay.setSpacing(6)
        top = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(); self.title.setStyleSheet("font-size:15px;font-weight:700;color:#ffffff;")
        x = QtWidgets.QPushButton("\u2715"); x.setFixedSize(24, 24); x.setCursor(QtCore.Qt.PointingHandCursor); x.clicked.connect(self.close_panel)
        top.addWidget(self.title, 1); top.addWidget(x)
        lay.addLayout(top)
        self.body = QtWidgets.QLabel(); self.body.setWordWrap(True); self.body.setTextFormat(QtCore.Qt.RichText)
        self.body.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        lay.addWidget(self.body, 1)
        self.timer = QtCore.QTimer(self); self.timer.setInterval(500); self.timer.timeout.connect(self.refresh)
        self.hide()

    def place(self):
        p = self.parentWidget()
        if p:
            w = min(340, max(260, p.width() // 3))
            self.setGeometry(p.width() - w - 14, 70, w, max(260, p.height() - 90))

    def open(self, key):
        self.key = key; self.place(); self.refresh(); self.show(); self.raise_(); self.timer.start()

    def close_panel(self):
        self.hide(); self.timer.stop(); self.key = None
        c = self.parentWidget()
        if c is not None and hasattr(c, "selected"):
            c.selected = None

    def hideEvent(self, e):
        self.timer.stop(); super().hideEvent(e)

    @staticmethod
    def text_for(info):
        import html, time as _t
        e = html.escape
        col = "#%02x%02x%02x" % tuple(bb.SPEC[info["key"]][5])
        now = "working: " + e(info["detail"] or "busy") if info["busy"] else ("loaded, waiting" if info["loaded"] else "idle")
        speed = f"{info['tok_s']:.0f} tokens/s" if info.get("tok_s") else "\u2014"
        rows = [f"<div style='color:{col};font-size:12px;font-weight:600'>{e(info['label'])} \u00b7 {e(info['role'])}</div>",
                f"<div style='margin-top:6px'><b>Now:</b> {now}</div>",
                f"<div><b>Model:</b> {e(str(info['model']))}</div>",
                f"<div><b>VRAM:</b> {e(info['vram_text'])} &nbsp; <b>Speed:</b> {speed}</div>",
                f"<div><b>Fired:</b> {info['fired']} times" + (f" &nbsp; <b>Tokens:</b> {info['tokens']}" if info.get("tokens") else "") + "</div>",
                "<div style='margin-top:10px;color:#b9a6ff;font-weight:600'>Recent</div>"]
        io = info.get("io") or []
        if not io:
            rows.append("<div style='color:#8f8fb3'>Nothing yet. It lights up the next time this part works.</div>")
        for t, d, txt in reversed(io[-6:]):
            ago = max(0, int(_t.time() - t))
            when = f"{ago}s" if ago < 60 else f"{ago // 60}m"
            arrow = "\u2192 in" if d == "in" else "\u2190 out"
            rows.append(f"<div style='margin-top:4px'><span style='color:{col}'>{arrow}</span> <span style='color:#8f8fb3'>{when} ago</span><br>{e(txt)}</div>")
        if info["key"] in bb.PRIVATE_REGIONS:
            rows.append("<div style='margin-top:8px;color:#8f8fb3;font-size:10px'>Personal details are hidden here.</div>")
        return "".join(rows)

    def refresh(self):
        if not self.key:
            return
        info = self.bus.region_info(self.key)
        if not info:
            return
        self.title.setText(info["role"])
        self.body.setText(self.text_for(info))
