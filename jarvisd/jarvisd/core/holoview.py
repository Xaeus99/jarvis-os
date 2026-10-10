"""v5.6 hologram workshop: the 3D viewer. A real 3D scene (perspective camera, orbit / zoom / pan, depth sorting,
lighting from face normals) drawn with a sci-fi hologram look: glowing wireframe over translucent shading,
scan lines, a projector disc, a soft flicker. Drag = orbit, right-drag = pan, wheel = zoom, double-click = reset,
auto-spin when idle. GPU-painted through QOpenGLWidget when OpenGL works, software otherwise (same picture).
30 fps cap; the timer stops when the window is hidden or minimised, and HoloHost minimises it during games."""
from __future__ import annotations
import math, os, time
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from . import holo

def _c(rgb, a=255):
    return QtGui.QColor(max(0, min(255, int(rgb[0]))), max(0, min(255, int(rgb[1]))), max(0, min(255, int(rgb[2]))), max(0, min(255, int(a))))

def _rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float32)

def _rot_axis(axis, a):
    x, y, z = axis / (np.linalg.norm(axis) or 1.0)
    c, s, C = math.cos(a), math.sin(a), 1 - math.cos(a)
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]], dtype=np.float32)

class _Compiled:
    __slots__ = ("V", "quads", "polys", "edges", "col", "alpha", "glow", "label", "center", "spin", "orbit", "orbit_axis",
                 "pulse", "tilt", "size")

def compile_scene(scene, detail=None):
    """scene dict -> list of compiled parts (meshes in world space, edges, colours, animation)."""
    parts = scene.get("parts", [])
    n = len(parts)
    detail = detail or (1.0 if n <= 40 else (0.7 if n <= 120 else 0.5))
    out = []
    for p in parts:
        V, F = holo.make_mesh(p, float(p.get("detail", 1.0)) * detail)
        c = _Compiled()
        c.V = V.astype(np.float32)
        q = [f for f in F if len(f) == 4]; o = [f for f in F if len(f) != 4]
        c.quads = np.array(q, dtype=np.int32).reshape(-1, 4)
        c.polys = o
        E = set()
        for f in F:
            for i in range(len(f)):
                a, b = f[i], f[(i + 1) % len(f)]
                if a != b:
                    E.add((min(a, b), max(a, b)))
        # drop degenerate edges at lathe poles (zero length)
        E = [e for e in E if float(np.sum((V[e[0]] - V[e[1]]) ** 2)) > 1e-8]
        c.edges = np.array(E, dtype=np.int32).reshape(-1, 2)
        c.col = holo.color(p.get("color"))
        c.alpha = float(p.get("alpha", 0.4)); c.glow = bool(p.get("glow")); c.label = p.get("label")
        c.center = np.array(holo._vec(p.get("pos", 0)), dtype=np.float32)
        c.spin = math.radians(float(p.get("spin", 0) or 0)); c.orbit = math.radians(float(p.get("orbit", 0) or 0))
        ax = p.get("orbit_axis") or (0, 1, 0)
        c.orbit_axis = np.array(ax, dtype=np.float32)
        c.pulse = float(p.get("pulse", 0) or 0); c.tilt = math.radians(float(p.get("tilt", 0) or 0))
        c.size = float(np.max(np.ptp(V, axis=0))) if len(V) else 1.0
        out.append(c)
    return out

def compile_lines(scene):
    out = []
    for ln in scene.get("lines", []):
        pts = np.array(ln.get("points") or [], dtype=np.float32).reshape(-1, 3)
        if len(pts) < 2:
            continue
        out.append(dict(P=pts, col=holo.color(ln.get("color", "#22d3ee")), width=float(ln.get("width", 1.0)),
                        alpha=float(ln.get("alpha", 0.9)), spin=math.radians(float(ln.get("spin", 0) or 0)),
                        tilt=math.radians(float(ln.get("tilt", 0) or 0)), sphere=ln.get("group") is not None))
    return out

LIGHT = np.array([0.35, 0.55, 0.76], dtype=np.float32); LIGHT /= np.linalg.norm(LIGHT)

class _Holo:
    def _init_holo(self, scene=None, fps=30, tint=None, clock=None):
        self.fps = max(10, min(30, int(fps or 30)))
        self.clock = clock or time.monotonic
        self.t0 = self.clock()
        self.tint = tint
        self.auto_spin = 0.25          # rad/s
        self.spin_speed = 1.0          # multiplier for the scene's own animations
        self.yaw = 0.6; self.pitch = math.radians(-12); self.dist = 7.0; self.target = np.zeros(3, np.float32)
        self.pan = np.zeros(3, np.float32)
        self._drag = None; self._last_input = 0.0
        self.scene = None; self.parts = []; self.lines = []
        self.scan = None
        self.frames = 0; self._fps_t = time.monotonic(); self.real_fps = 0.0
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.CoarseTimer)
        self.timer.timeout.connect(self.update)
        self.setMinimumSize(360, 260)
        if scene:
            self.set_scene(scene)

    # ---- scene ----
    def set_scene(self, scene, keep_camera=False):
        self.scene = scene
        self.parts = compile_scene(scene)
        self.lines = compile_lines(scene)
        cam = scene.get("camera") or {}
        if not keep_camera:
            self.dist = float(cam.get("dist", 7.0)); self.pitch = math.radians(float(cam.get("pitch", -12)))
            self.target = np.array(cam.get("target", (0, 0, 0)), dtype=np.float32); self.pan[:] = 0
            self.yaw = 0.6
        self._home = (self.yaw, self.pitch, self.dist)
        ys = [float(np.min(c.V[:, 1])) for c in self.parts if len(c.V)]
        self.floor_y = (min(ys) - 0.25) if ys else -1.5
        span = [float(np.max(np.abs(c.V[:, [0, 2]] - self.target[[0, 2]]))) for c in self.parts if len(c.V)]
        self.floor_r = max(1.2, min(12.0, (max(span) if span else 2.0) * 0.9))
        self.update()

    def now(self):
        return self.clock() - self.t0

    # ---- lifecycle ----
    def showEvent(self, e):
        self.timer.start(int(1000 / self.fps)); super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop(); super().hideEvent(e)

    def resizeEvent(self, e):
        self.scan = None; super().resizeEvent(e)

    # ---- input ----
    def mousePressEvent(self, e):
        self._drag = (e.button(), e.position(), self.yaw, self.pitch, self.pan.copy()); self._last_input = self.clock()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        b, p0, yaw, pitch, pan = self._drag
        d = e.position() - p0
        if b == QtCore.Qt.LeftButton:
            self.yaw = yaw + d.x() * 0.01
            self.pitch = max(-1.45, min(1.45, pitch + d.y() * 0.008))
        else:
            k = self.dist / max(200.0, self.height())
            c, s = math.cos(self.yaw), math.sin(self.yaw)
            self.pan = pan + np.array([-d.x() * k * c, d.y() * k, -d.x() * k * s], dtype=np.float32)
        self._last_input = self.clock(); self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def wheelEvent(self, e):
        k = 0.88 if e.angleDelta().y() > 0 else 1.13
        self.dist = max(1.5, min(80.0, self.dist * k)); self._last_input = self.clock(); self.update()

    def mouseDoubleClickEvent(self, e):
        self.yaw, self.pitch, self.dist = self._home; self.pan[:] = 0; self.update()

    def zoom(self, factor):
        self.dist = max(1.5, min(80.0, self.dist / max(0.1, factor))); self.update()

    # ---- painting ----
    def _scanlines(self, w, h):
        pm = QtGui.QPixmap(w, h); pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setPen(QtGui.QPen(QtGui.QColor(120, 220, 255, 16), 1))
        for y in range(0, h, 3):
            p.drawLine(0, y, w, y)
        p.end()
        return pm

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        try:
            self.paint_frame(p, self.width(), self.height())
        finally:
            p.end()

    def paint_frame(self, p, w, h, t=None):
        t = self.now() if t is None else t
        if self._drag is None and self.clock() - self._last_input > 2.5:
            self.yaw += self.auto_spin / self.fps
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        g = QtGui.QRadialGradient(w / 2, h * 0.55, max(w, h) * 0.8)
        g.setColorAt(0, QtGui.QColor(10, 22, 40)); g.setColorAt(0.6, QtGui.QColor(4, 8, 18)); g.setColorAt(1, QtGui.QColor(1, 2, 6))
        p.fillRect(0, 0, w, h, g)
        if self.scene is None:
            return
        flick = 0.94 + 0.06 * math.sin(t * 37.0) * math.sin(t * 3.1)
        cx, cy, f = w / 2, h / 2, min(w, h) * 1.15
        cyaw, syaw, cp, sp = math.cos(self.yaw), math.sin(self.yaw), math.cos(self.pitch), math.sin(self.pitch)
        Rcam = np.array([[cyaw, 0, -syaw], [0, 1, 0], [syaw, 0, cyaw]], dtype=np.float32)
        Rcam = np.array([[1, 0, 0], [0, cp, sp], [0, -sp, cp]], dtype=np.float32) @ Rcam
        tgt = self.target + self.pan
        D = self.dist
        def cam(X):
            C = (X - tgt) @ Rcam.T
            return C
        def proj(C):
            depth = np.maximum(0.05, D - C[:, 2])
            return np.stack([cx + C[:, 0] * f / depth, cy - C[:, 1] * f / depth], axis=1), depth
        T = t * self.spin_speed
        spin_all = math.radians(float(self.scene.get("spin_all", 0) or 0)) * T
        Rall = _rot_y(spin_all) if spin_all else None

        # projector disc (a real 3D ring on the floor)
        rings = []
        for k, rr in enumerate((self.floor_r, self.floor_r * 0.72, self.floor_r * 0.4)):
            pts = np.array([(tgt[0] + rr * math.cos(a), self.floor_y, tgt[2] + rr * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 72)], dtype=np.float32)
            S, _ = proj(cam(pts))
            rings.append((S, k))
        for S, k in rings:
            p.setPen(QtGui.QPen(_c((80, 200, 255), (70, 45, 30)[k] * flick), 1.4 if k == 0 else 1.0))
            p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in S]))
        Sc, _ = proj(cam(np.array([[tgt[0], self.floor_y, tgt[2]]], dtype=np.float32)))
        gr = QtGui.QRadialGradient(Sc[0][0], Sc[0][1], self.floor_r * f / D)
        gr.setColorAt(0, _c((80, 200, 255), 55)); gr.setColorAt(1, _c((80, 200, 255), 0))
        p.setPen(QtCore.Qt.NoPen); p.setBrush(gr)
        p.drawEllipse(QtCore.QPointF(Sc[0][0], Sc[0][1]), self.floor_r * f / D, self.floor_r * f / D * 0.35)

        tint = holo.color(self.tint) if self.tint else None
        # animate + transform every part, then sort far -> near
        frame = []
        for c in self.parts:
            V = c.V
            if c.spin or c.tilt:
                R = _rot_y(c.spin * T)
                if c.tilt:
                    ct, st = math.cos(c.tilt), math.sin(c.tilt)
                    R = np.array([[ct, -st, 0], [st, ct, 0], [0, 0, 1]], dtype=np.float32) @ R
                V = (V - c.center) @ R.T + c.center
            if c.orbit:
                V = V @ _rot_axis(c.orbit_axis, c.orbit * T).T
            if Rall is not None:
                V = V @ Rall.T
            C = cam(V)
            S, depth = proj(C)
            frame.append((float(np.mean(depth)), c, C, S))
        frame.sort(key=lambda x: -x[0])
        labels = []
        for dmean, c, C, S in frame:
            col = tint or c.col
            fog = max(0.35, min(1.0, 1.25 - (dmean - D) / (D * 0.9 + 1e-6) * 0.6))
            puls = (0.75 + 0.25 * math.sin(t * c.pulse * 2 * math.pi)) if c.pulse else 1.0
            a_fill = c.alpha * fog * flick * puls
            # faces: normals in camera space, back faces skipped for the fill, 4 shade bins
            if len(c.quads):
                Q = c.quads
                n = np.cross(C[Q[:, 1]] - C[Q[:, 0]], C[Q[:, 3]] - C[Q[:, 0]])
                ln = np.linalg.norm(n, axis=1) + 1e-9
                n = n / ln[:, None]
                tocam = np.array([0, 0, D], dtype=np.float32) - C[Q[:, 0]]
                front = np.einsum("ij,ij->i", n, tocam) > 0
                shade = np.abs(n @ LIGHT)
                bins = np.minimum(3, (shade * 4).astype(int))
                SQ = S[Q]
                for b in range(4):
                    sel = np.nonzero(front & (bins == b) & (ln > 1e-7))[0]
                    if not len(sel):
                        continue
                    path = QtGui.QPainterPath(); path.setFillRule(QtCore.Qt.WindingFill)
                    for quad in SQ[sel].tolist():
                        path.addPolygon(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in quad]))
                    k = 0.35 + 0.22 * b
                    p.setPen(QtCore.Qt.NoPen)
                    p.setBrush(_c((col[0] * k + 30, col[1] * k + 30, col[2] * k + 40), 255 * a_fill * (0.55 + 0.15 * b)))
                    p.drawPath(path)
            for poly in c.polys:
                pts = S[list(poly)]
                p.setPen(QtCore.Qt.NoPen); p.setBrush(_c(col, 255 * a_fill * 0.6))
                p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in pts.tolist()]))
            # glowing wireframe: a wide soft pass and a thin bright pass
            if len(c.edges):
                E = S[c.edges].tolist()
                lines = [QtCore.QLineF(a[0], a[1], b[0], b[1]) for a, b in E]
                bright = (min(255, col[0] * 0.6 + 110), min(255, col[1] * 0.6 + 110), min(255, col[2] * 0.6 + 120))
                p.setPen(QtGui.QPen(_c(col, 60 * fog * flick * puls), 2.6)); p.drawLines(lines)
                p.setPen(QtGui.QPen(_c(bright, 175 * fog * flick * puls), 0.8)); p.drawLines(lines)
            if c.glow:
                ctr = S.mean(axis=0); r = max(6.0, min(90.0, c.size * f / dmean * 0.9))
                g2 = QtGui.QRadialGradient(ctr[0], ctr[1], r)
                g2.setColorAt(0, _c((255, 255, 255), 200 * puls)); g2.setColorAt(0.3, _c(col, 150 * puls)); g2.setColorAt(1, _c(col, 0))
                p.setPen(QtCore.Qt.NoPen); p.setBrush(g2); p.drawEllipse(QtCore.QPointF(ctr[0], ctr[1]), r, r)
            if c.label:
                labels.append((dmean, c.label, S.mean(axis=0), col))
        # 3D polylines (coastlines, orbits); the far side of a globe is dimmed
        for ln in self.lines:
            X = ln["P"]
            if ln["spin"] or ln["tilt"]:
                R = _rot_y(ln["spin"] * T)
                if ln["tilt"]:
                    ct, st = math.cos(ln["tilt"]), math.sin(ln["tilt"])
                    R = np.array([[ct, -st, 0], [st, ct, 0], [0, 0, 1]], dtype=np.float32) @ R
                X = X @ R.T
            if Rall is not None:
                X = X @ Rall.T
            C = cam(X); S, _ = proj(C)
            col = tint or ln["col"]
            if ln["sphere"]:
                ctr = cam(np.zeros((1, 3), np.float32))[0]
                tocam = np.array([0, 0, D], np.float32) - C
                front = np.einsum("ij,ij->i", C - ctr, tocam) > 0
            else:
                front = np.ones(len(C), bool)
            for vis, al in ((False, 0.18), (True, 1.0)):
                path = QtGui.QPainterPath(); started = False
                for i in range(len(S)):
                    if front[i] == vis:
                        pt = QtCore.QPointF(S[i][0], S[i][1])
                        if started:
                            path.lineTo(pt)
                        else:
                            path.moveTo(pt); started = True
                    else:
                        started = False
                p.setBrush(QtCore.Qt.NoBrush)
                p.setPen(QtGui.QPen(_c(col, 255 * ln["alpha"] * al * flick), ln["width"] * (1.0 if vis else 0.8)))
                p.drawPath(path)
        # glows (atmosphere, sun, exhaust)
        for gl in self.scene.get("glows", []):
            pos = np.array([gl.get("pos", (0, 0, 0))], np.float32)
            if Rall is not None:
                pos = pos @ Rall.T
            C = cam(pos); S, d = proj(C)
            r = float(gl.get("radius", 1.0)) * f / float(d[0])
            col = tint or holo.color(gl.get("color", "#60a5fa"))
            g3 = QtGui.QRadialGradient(S[0][0], S[0][1], r * 1.25)
            if gl.get("kind") == "atmosphere":
                g3.setColorAt(0.0, _c(col, 0)); g3.setColorAt(0.70, _c(col, 10)); g3.setColorAt(0.80, _c(col, 90 * flick)); g3.setColorAt(1.0, _c(col, 0))
            else:
                g3.setColorAt(0, _c(col, 140)); g3.setColorAt(1, _c(col, 0))
            p.setPen(QtCore.Qt.NoPen); p.setBrush(g3); p.drawEllipse(QtCore.QPointF(S[0][0], S[0][1]), r * 1.25, r * 1.25)
        # labels: callouts for the nearest 14, plus scene labels
        for lb in self.scene.get("labels", []):
            pos = np.array([lb.get("pos", (0, 0, 0))], np.float32)
            C = cam(pos); S, d = proj(C)
            labels.append((float(d[0]), lb.get("text", ""), S[0], (200, 230, 255)))
        labels.sort(key=lambda x: x[0])
        fnt = QtGui.QFont("Segoe UI", 9); fnt.setBold(True); p.setFont(fnt); fm = QtGui.QFontMetrics(fnt)
        for dmean, text, pt, col in labels[:14]:
            side = 1 if pt[0] >= cx else -1
            ex, ey = pt[0] + side * 46, pt[1] - 26
            p.setPen(QtGui.QPen(_c((180, 235, 255), 170), 1))
            p.drawLine(QtCore.QPointF(pt[0], pt[1]), QtCore.QPointF(ex, ey)); p.drawLine(QtCore.QPointF(ex, ey), QtCore.QPointF(ex + side * 30, ey))
            p.setBrush(_c((180, 235, 255), 220)); p.drawEllipse(QtCore.QPointF(pt[0], pt[1]), 2.4, 2.4)
            tw = fm.horizontalAdvance(text)
            p.setPen(_c((225, 245, 255), 235))
            p.drawText(QtCore.QPointF(ex + side * 34 - (tw if side < 0 else 0), ey + 4), text)
        # scan lines + a sweeping band
        if self.scan is None or self.scan.size() != QtCore.QSize(w, h):
            self.scan = self._scanlines(w, h)
        p.drawPixmap(0, 0, self.scan)
        by = (t * 90.0) % (h + 120) - 60
        bg = QtGui.QLinearGradient(0, by - 40, 0, by + 40)
        bg.setColorAt(0, _c((120, 220, 255), 0)); bg.setColorAt(0.5, _c((120, 220, 255), 22)); bg.setColorAt(1, _c((120, 220, 255), 0))
        p.fillRect(QtCore.QRectF(0, by - 40, w, 80), bg)
        # header
        f1 = QtGui.QFont("Segoe UI", 14); f1.setBold(True); p.setFont(f1)
        p.setPen(_c((215, 240, 255), 240)); p.drawText(QtCore.QPointF(22, 36), "HOLOGRAM \u00b7 " + str(self.scene.get("title", "")).upper())
        p.setFont(QtGui.QFont("Segoe UI", 9)); p.setPen(_c((176, 102, 255), 230))
        p.drawText(QtCore.QPointF(22, 56), f"{len(self.parts)} parts \u00b7 drag to orbit \u00b7 right-drag to pan \u00b7 scroll to zoom \u00b7 double-click to reset")
        self.frames += 1
        now = time.monotonic()
        if now - self._fps_t >= 1.0:
            self.real_fps = self.frames / (now - self._fps_t); self.frames = 0; self._fps_t = now


class HoloCanvas(_Holo, QtWidgets.QWidget):
    gl = False
    def __init__(self, scene=None, fps=30, tint=None, parent=None, clock=None):
        QtWidgets.QWidget.__init__(self, parent)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self._init_holo(scene, fps, tint, clock)

_GL = None
def _gl_class():
    global _GL
    if _GL is None:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
        class HoloCanvasGL(_Holo, QOpenGLWidget):
            gl = True
            def __init__(self, scene=None, fps=30, tint=None, parent=None, clock=None):
                QOpenGLWidget.__init__(self, parent)
                fmt = QtGui.QSurfaceFormat(); fmt.setSamples(4); self.setFormat(fmt)
                self._init_holo(scene, fps, tint, clock)
            def paintEvent(self, e):
                QOpenGLWidget.paintEvent(self, e)
            def paintGL(self):
                p = QtGui.QPainter(self)
                try:
                    self.paint_frame(p, self.width(), self.height())
                finally:
                    p.end()
        _GL = HoloCanvasGL
    return _GL

def make_canvas(scene=None, fps=30, tint=None, parent=None, prefer_gl=True):
    try:
        plat = QtGui.QGuiApplication.platformName()
    except Exception:
        plat = ""
    if prefer_gl and plat not in ("offscreen", "minimal", "vnc") and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        try:
            return _gl_class()(scene, fps, tint, parent)
        except Exception:
            pass
    return HoloCanvas(scene, fps, tint, parent)

def render_png(scene, path, w=1280, h=800, yaw=None, pitch=None, t=1.0, tint=None):
    """Snapshot a scene to a PNG (the "save this model" picture; also used by the tests)."""
    c = HoloCanvas(scene, tint=tint)
    c.resize(w, h)
    if yaw is not None:
        c.yaw = yaw
    if pitch is not None:
        c.pitch = math.radians(pitch)
    c._last_input = c.clock()          # no auto-spin for a still
    img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
    p = QtGui.QPainter(img)
    try:
        c.paint_frame(p, w, h, t=t)
    finally:
        p.end()
    img.save(path)
    c.deleteLater()
    return path


class HoloWindow(QtWidgets.QWidget):
    closed = QtCore.Signal()
    def __init__(self, scene=None, fps=30):
        super().__init__(None, QtCore.Qt.Window)
        self.setWindowTitle("J.A.R.V.I.S. \u2014 Hologram Workshop")
        self.canvas = make_canvas(scene, fps, parent=self)
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.canvas)
        self.resize(1180, 760)
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


class HoloHost(QtCore.QObject):
    """Owns the hologram window on the Qt thread. Never opens during a game; minimises when one starts."""
    _show = QtCore.Signal(object)
    _hide = QtCore.Signal()
    _call = QtCore.Signal(object)

    def __init__(self, is_gaming=lambda: False, fps=30, parent=None):
        super().__init__(parent)
        self.is_gaming = is_gaming; self.fps = fps
        self.win = None; self.scene = None
        self._show.connect(self._do_show); self._hide.connect(self._do_hide); self._call.connect(lambda fn: fn())
        self.timer = QtCore.QTimer(self); self.timer.setInterval(2000); self.timer.timeout.connect(self._tick)

    def show(self, scene):
        self.scene = scene; self._show.emit(scene)

    def hide(self):
        self._hide.emit()

    def on_qt(self, fn):
        self._call.emit(fn)

    def is_open(self):
        return bool(self.win and self.win.isVisible())

    def _do_show(self, scene):
        if self.win is None:
            self.win = HoloWindow(scene, self.fps)
            self.win.closed.connect(self._closed)
        else:
            self.win.canvas.set_scene(scene)
        if self.win.isMinimized():
            self.win.showNormal()
        self.win.show(); self.win.raise_()
        self.timer.start()

    def _closed(self):
        self.timer.stop(); self.win = None

    def _do_hide(self):
        if self.win:
            self.win.close()

    def _tick(self):
        try:
            if self.win and self.is_gaming() and not self.win.isMinimized():
                self.win.showMinimized()
        except Exception:
            pass
