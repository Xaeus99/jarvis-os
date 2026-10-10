"""v5.6 HUDs: "Jarvis, show vitals" (live CPU / GPU / RAM / VRAM / temperatures / network graphs) and the update
countdown ring ("when's the next update?"). Both poll only while open, cap at 30 fps, never take focus from a game
and are hidden or minimised while one runs."""
from __future__ import annotations
import collections, datetime, math, subprocess, sys, threading, time
from PySide6 import QtCore, QtGui, QtWidgets

PURPLE = (176, 102, 255); CYAN = (80, 220, 255); GOLD = (245, 197, 66); GREEN = (74, 222, 128); PINK = (244, 114, 182)

def _c(rgb, a=255):
    return QtGui.QColor(max(0, min(255, int(rgb[0]))), max(0, min(255, int(rgb[1]))), max(0, min(255, int(rgb[2]))), max(0, min(255, int(a))))

def gpu_stats(run=None):
    """-> dict(util, mem_used, mem_total (MB), temp, power, name) from nvidia-smi, or {}. Hidden window, 2 s timeout."""
    run = run or subprocess.run
    try:
        kw = dict(capture_output=True, text=True, timeout=2)
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000
        q = "utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,name"
        p = run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"], **kw)
        f = [x.strip() for x in (p.stdout or "").strip().splitlines()[0].split(",", 5)]
        num = lambda s: float(s) if s and s.replace(".", "", 1).isdigit() else None
        return dict(util=num(f[0]), mem_used=num(f[1]), mem_total=num(f[2]), temp=num(f[3]), power=num(f[4]), name=f[5])
    except Exception:
        return {}

class VitalsSampler:
    """One sample a second while the vitals window is open. 120 s of history."""
    def __init__(self, gpu=gpu_stats, psutil_mod=None, keep=120):
        self.gpu = gpu
        self.ps = psutil_mod
        self.h = {k: collections.deque(maxlen=keep) for k in ("cpu", "ram", "gpu", "vram", "net_down", "net_up", "cpu_temp", "gpu_temp")}
        self.last = {}; self._net = None
        self.lock = threading.Lock()

    def _psutil(self):
        if self.ps is None:
            import psutil
            self.ps = psutil
        return self.ps

    def sample(self):
        d = {}
        try:
            ps = self._psutil()
            d["cpu"] = ps.cpu_percent(None)
            vm = ps.virtual_memory(); d["ram"] = vm.percent; d["ram_used"] = vm.used / 2**30; d["ram_total"] = vm.total / 2**30
            n = ps.net_io_counters(); now = time.monotonic()
            if self._net:
                dt = max(0.1, now - self._net[0])
                d["net_down"] = max(0.0, (n.bytes_recv - self._net[1]) / dt / 2**20 * 8)    # Mbit/s
                d["net_up"] = max(0.0, (n.bytes_sent - self._net[2]) / dt / 2**20 * 8)
            self._net = (now, n.bytes_recv, n.bytes_sent)
            try:
                temps = getattr(ps, "sensors_temperatures", lambda: {})() or {}
                vals = [t.current for arr in temps.values() for t in arr if getattr(t, "current", None)]
                if vals:
                    d["cpu_temp"] = max(vals)
            except Exception:
                pass
        except Exception:
            pass
        g = self.gpu() or {}
        if g.get("util") is not None:
            d["gpu"] = g["util"]
        if g.get("mem_used") is not None and g.get("mem_total"):
            d["vram"] = 100.0 * g["mem_used"] / g["mem_total"]; d["vram_used"] = g["mem_used"] / 1024; d["vram_total"] = g["mem_total"] / 1024
        if g.get("temp") is not None:
            d["gpu_temp"] = g["temp"]
        if g.get("power") is not None:
            d["gpu_power"] = g["power"]
        d["gpu_name"] = g.get("name")
        with self.lock:
            for k, q in self.h.items():
                if d.get(k) is not None:
                    q.append(float(d[k]))
            self.last = d
        return d

class VitalsWindow(QtWidgets.QWidget):
    closed = QtCore.Signal()
    PANELS = [("cpu", "CPU", "%", CYAN, 100), ("gpu", "GPU", "%", PURPLE, 100), ("ram", "RAM", "%", GREEN, 100),
              ("vram", "VRAM", "%", PINK, 100), ("cpu_temp", "CPU TEMP", "\u00b0C", GOLD, 100), ("gpu_temp", "GPU TEMP", "\u00b0C", GOLD, 100),
              ("net_down", "NET \u2193", "Mb/s", CYAN, None), ("net_up", "NET \u2191", "Mb/s", PURPLE, None)]

    def __init__(self, sampler=None, fps=20, every=1.0, start_thread=True):
        super().__init__(None, QtCore.Qt.Window)
        self.setWindowTitle("J.A.R.V.I.S. \u2014 System Vitals")
        self.s = sampler or VitalsSampler()
        self.every = every; self.t0 = time.monotonic()
        self._stop = threading.Event(); self.th = None
        self.timer = QtCore.QTimer(self); self.timer.setInterval(int(1000 / max(5, min(30, fps)))); self.timer.timeout.connect(self.update)
        self.resize(980, 600)
        self.start_thread = start_thread

    def showEvent(self, e):
        self.timer.start()
        if self.start_thread and not (self.th and self.th.is_alive()):
            self._stop.clear()
            def loop():
                while not self._stop.is_set():
                    self.s.sample(); self._stop.wait(self.every)
            self.th = threading.Thread(target=loop, name="vitals", daemon=True); self.th.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop(); self._stop.set(); super().hideEvent(e)

    def closeEvent(self, e):
        self._stop.set(); self.closed.emit(); super().closeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Escape:
            self.close()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        try:
            self.paint(p, self.width(), self.height())
        finally:
            p.end()

    def paint(self, p, w, h):
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        g = QtGui.QLinearGradient(0, 0, 0, h); g.setColorAt(0, QtGui.QColor(10, 10, 26)); g.setColorAt(1, QtGui.QColor(3, 3, 10))
        p.fillRect(0, 0, w, h, g)
        t = time.monotonic() - self.t0
        with self.s.lock:
            last = dict(self.s.last); hist = {k: list(v) for k, v in self.s.h.items()}
        f = QtGui.QFont("Segoe UI", 14); f.setBold(True); p.setFont(f); p.setPen(_c((230, 235, 255), 240))
        p.drawText(QtCore.QPointF(24, 38), "J.A.R.V.I.S.  SYSTEM VITALS")
        p.setFont(QtGui.QFont("Segoe UI", 9)); p.setPen(_c(PURPLE, 230))
        sub = (last.get("gpu_name") or "GPU") + (f" \u00b7 {last['gpu_power']:.0f} W" if last.get("gpu_power") else "")
        sub += f" \u00b7 RAM {last['ram_used']:.1f}/{last['ram_total']:.0f} GB" if last.get("ram_total") else ""
        sub += f" \u00b7 VRAM {last['vram_used']:.1f}/{last['vram_total']:.0f} GB" if last.get("vram_total") else ""
        p.drawText(QtCore.QPointF(24, 58), sub)
        cols, rows = 4, 2; pad = 16; top = 76
        pw = (w - pad * (cols + 1)) / cols; ph = (h - top - pad * (rows + 1)) / rows
        for i, (k, name, unit, col, vmax) in enumerate(self.PANELS):
            x = pad + (i % cols) * (pw + pad); y = top + pad + (i // cols) * (ph + pad)
            self.panel(p, QtCore.QRectF(x, y, pw, ph), name, unit, col, hist.get(k, []), last.get(k), vmax, t)
        # sweeping scan line
        sy = (t * 60) % h
        sg = QtGui.QLinearGradient(0, sy - 30, 0, sy + 30)
        sg.setColorAt(0, _c(CYAN, 0)); sg.setColorAt(0.5, _c(CYAN, 14)); sg.setColorAt(1, _c(CYAN, 0))
        p.fillRect(QtCore.QRectF(0, sy - 30, w, 60), sg)

    def panel(self, p, r, name, unit, col, hist, val, vmax, t):
        path = QtGui.QPainterPath(); path.addRoundedRect(r, 12, 12)
        p.setPen(QtGui.QPen(_c(col, 90), 1.2)); p.setBrush(_c((18, 16, 40), 200)); p.drawPath(path)
        p.setFont(QtGui.QFont("Segoe UI", 9, QtGui.QFont.Bold)); p.setPen(_c(col, 230))
        p.drawText(QtCore.QPointF(r.x() + 14, r.y() + 22), name)
        big = QtGui.QFont("Segoe UI", 22); big.setBold(True); p.setFont(big); p.setPen(_c((240, 244, 255), 240))
        txt = "n/a" if val is None else (f"{val:.0f}" if (vmax or val >= 10) else f"{val:.1f}")
        p.drawText(QtCore.QPointF(r.x() + 14, r.y() + 56), txt)
        if val is not None:
            fm = QtGui.QFontMetrics(big); p.setFont(QtGui.QFont("Segoe UI", 10)); p.setPen(_c(col, 220))
            p.drawText(QtCore.QPointF(r.x() + 18 + fm.horizontalAdvance(txt), r.y() + 56), unit)
        # ring gauge (top right)
        if vmax and val is not None:
            rr = min(r.height() * 0.22, 30); c = QtCore.QPointF(r.right() - rr - 16, r.y() + rr + 14)
            p.setPen(QtGui.QPen(_c(col, 50), 5)); p.setBrush(QtCore.Qt.NoBrush); p.drawEllipse(c, rr, rr)
            p.setPen(QtGui.QPen(_c(col, 230), 5, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawArc(QtCore.QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), 90 * 16, -int(360 * 16 * max(0.0, min(1.0, val / vmax))))
        # graph with glow fill
        gr = QtCore.QRectF(r.x() + 12, r.y() + 70, r.width() - 24, r.height() - 82)
        p.setPen(QtGui.QPen(_c((120, 120, 180), 30), 1))
        for k in range(1, 4):
            yy = gr.y() + gr.height() * k / 4; p.drawLine(QtCore.QPointF(gr.x(), yy), QtCore.QPointF(gr.right(), yy))
        if len(hist) >= 2:
            top = vmax or max(1.0, max(hist) * 1.2)
            n = 120
            pts = [QtCore.QPointF(gr.right() - (len(hist) - 1 - i) * gr.width() / (n - 1), gr.bottom() - gr.height() * max(0.0, min(1.0, v / top)))
                   for i, v in enumerate(hist)]
            line = QtGui.QPainterPath(pts[0])
            for q in pts[1:]:
                line.lineTo(q)
            fill = QtGui.QPainterPath(line); fill.lineTo(pts[-1].x(), gr.bottom()); fill.lineTo(pts[0].x(), gr.bottom()); fill.closeSubpath()
            lg = QtGui.QLinearGradient(0, gr.y(), 0, gr.bottom()); lg.setColorAt(0, _c(col, 90)); lg.setColorAt(1, _c(col, 0))
            p.setPen(QtCore.Qt.NoPen); p.setBrush(lg); p.drawPath(fill)
            p.setBrush(QtCore.Qt.NoBrush)
            p.setPen(QtGui.QPen(_c(col, 60), 5)); p.drawPath(line)
            p.setPen(QtGui.QPen(_c(col, 240), 1.6)); p.drawPath(line)
            p.setBrush(_c((255, 255, 255), 230)); p.setPen(QtCore.Qt.NoPen); p.drawEllipse(pts[-1], 3, 3)


# ---------------------------------------------------------------- the update countdown
def parse_eta(iso):
    try:
        d = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.astimezone()
        return d
    except Exception:
        return None

def countdown_text(eta, now=None):
    """-> ('12:34', seconds_left) or ('finishing up\u2026', <=0)."""
    d = parse_eta(eta.get("eta_iso")) if isinstance(eta, dict) else None
    if d is None:
        return "no ETA", None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    left = (d - now).total_seconds()
    if left <= 0:
        return "finishing up\u2026", left
    m, s = divmod(int(left), 60)
    hh, m = divmod(m, 60)
    return (f"{hh}:{m:02d}:{s:02d}" if hh else f"{m:02d}:{s:02d}"), left

class CountdownWindow(QtWidgets.QWidget):
    """A holographic ring: version, % done, a live mm:ss countdown and the step. Shows without taking focus."""
    closed = QtCore.Signal()

    def __init__(self, get_eta, fps=20):
        super().__init__(None, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.get_eta = get_eta; self.t0 = time.monotonic()
        self.timer = QtCore.QTimer(self); self.timer.setInterval(int(1000 / max(5, min(30, fps)))); self.timer.timeout.connect(self.update)
        self.resize(420, 420)
        self._drag = None

    def place_corner(self):
        scr = QtGui.QGuiApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry(); self.move(g.right() - self.width() - 24, g.top() + 24)

    def showEvent(self, e):
        self.timer.start(); super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop(); super().hideEvent(e)

    def closeEvent(self, e):
        self.closed.emit(); super().closeEvent(e)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.RightButton:
            self.close(); return
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        try:
            self.paint(p, self.width(), self.height(), time.monotonic() - self.t0)
        finally:
            p.end()

    def paint(self, p, w, h, t, now=None):
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        eta = self.get_eta() or {}
        cx, cy = w / 2, h / 2; R = min(w, h) * 0.40
        bg = QtGui.QRadialGradient(cx, cy, R * 1.25)
        bg.setColorAt(0, _c((14, 12, 34), 235)); bg.setColorAt(0.85, _c((8, 8, 22), 225)); bg.setColorAt(1, _c((8, 8, 22), 0))
        p.setPen(QtCore.Qt.NoPen); p.setBrush(bg); p.drawEllipse(QtCore.QPointF(cx, cy), R * 1.25, R * 1.25)
        pct = max(0, min(100, int(eta.get("percent") or 0)))
        text, left = countdown_text(eta, now)
        # outer rotating tick ring
        p.setPen(QtGui.QPen(_c(CYAN, 110), 1.4))
        for i in range(72):
            a = math.radians(i * 5 + t * 12); r0 = R * (1.08 if i % 6 else 1.03)
            p.drawLine(QtCore.QPointF(cx + math.cos(a) * r0, cy + math.sin(a) * r0), QtCore.QPointF(cx + math.cos(a) * R * 1.12, cy + math.sin(a) * R * 1.12))
        # progress ring (percent) with glow
        rect = QtCore.QRectF(cx - R, cy - R, 2 * R, 2 * R)
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(QtGui.QPen(_c(PURPLE, 45), 14)); p.drawEllipse(QtCore.QPointF(cx, cy), R, R)
        span = -int(360 * 16 * pct / 100)
        p.setPen(QtGui.QPen(_c(PURPLE, 70), 22, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)); p.drawArc(rect, 90 * 16, span)
        p.setPen(QtGui.QPen(_c((200, 150, 255), 245), 10, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)); p.drawArc(rect, 90 * 16, span)
        # inner seconds ring (sweeps once a minute)
        if left is not None and left > 0:
            r2 = R * 0.80; frac = (left % 60) / 60.0
            p.setPen(QtGui.QPen(_c(CYAN, 220), 3, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            p.drawArc(QtCore.QRectF(cx - r2, cy - r2, 2 * r2, 2 * r2), 90 * 16, -int(360 * 16 * frac))
        ver = str(eta.get("version") or "?")
        f = QtGui.QFont("Segoe UI", 11); f.setBold(True); p.setFont(f); p.setPen(_c((200, 210, 255), 230))
        p.drawText(QtCore.QRectF(0, cy - R * 0.62, w, 22), QtCore.Qt.AlignCenter, f"NEXT UPDATE \u00b7 v{ver}")
        big = QtGui.QFont("Segoe UI", 34 if left is None or left > 0 else 20); big.setBold(True); p.setFont(big)
        p.setPen(_c((240, 245, 255), 250 if (left or 1) > 0 else int(160 + 90 * abs(math.sin(t * 2)))))
        p.drawText(QtCore.QRectF(0, cy - 34, w, 56), QtCore.Qt.AlignCenter, text)
        p.setFont(QtGui.QFont("Segoe UI", 12, QtGui.QFont.Bold)); p.setPen(_c(PURPLE, 240))
        p.drawText(QtCore.QRectF(0, cy + 22, w, 22), QtCore.Qt.AlignCenter, f"{pct}% built")
        step = str(eta.get("step") or "")
        p.setFont(QtGui.QFont("Segoe UI", 9)); p.setPen(_c((170, 220, 255), 220))
        p.drawText(QtCore.QRectF(cx - R * 0.7, cy + 48, R * 1.4, 40), QtCore.Qt.AlignHCenter | QtCore.Qt.TextWordWrap, step[:80])
