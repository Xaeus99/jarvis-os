"""The J.A.R.V.I.S. HUD: a small, always-on-top ring display in a screen corner (solid by default)."""
from __future__ import annotations
import math, os, time, logging
from PySide6 import QtCore, QtGui, QtWidgets
from . import theme as T

CYAN = QtGui.QColor(92, 225, 255)
ICE = QtGui.QColor(170, 240, 255)
DEEP = QtGui.QColor(20, 120, 170)
AMBER = QtGui.QColor(255, 170, 40)
STATES = ("idle", "listening", "thinking", "speaking", "paused")
log = logging.getLogger("jarvis.overlay")

def _c(col, a):
    c = QtGui.QColor(col); c.setAlphaF(max(0.0, min(1.0, a))); return c

def _arc(p, rect, start_deg, span_deg, pen):
    p.setPen(pen); p.setBrush(QtCore.Qt.NoBrush)
    p.drawArc(rect, int(-start_deg * 16), int(-span_deg * 16))

def paint_hud(p: QtGui.QPainter, size: int, t: float, state: str = "idle", level: float = 0.0, caption: str = "",
              tag: str = "", tag_color=None, cap_h: int = 24):
    """Draw the HUD into a size x (size+28) area. t = seconds, level = mic loudness 0..1.
    v4: colours follow the current theme (theme.py) and text uses the bundled Orbitron/Rajdhani."""
    CYAN, ICE, DEEP, AMBER = T.main(), T.light(), T.deep(), T.accent()
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    s = size; cx = cy = s / 2.0; R = s / 2.0 - 3
    active = state in ("listening", "thinking", "speaking")
    paused = state == "paused"
    glow = 1.0
    if state == "listening":
        glow = 1.25 + 0.25 * math.sin(t * 6.0) + 0.6 * level
    elif state == "thinking":
        glow = 1.15
    elif state == "speaking":
        glow = 1.15 + 0.2 * math.sin(t * 9.0)
    base_a = 0.35 if paused else (0.78 if not active else 1.0)
    speed = 0.0 if paused else (1.0 if state == "idle" else 3.0 if state == "listening" else 6.0 if state == "thinking" else 2.0)
    main = QtGui.QColor(140, 160, 170) if paused else CYAN
    accent = QtGui.QColor(150, 150, 150) if paused else AMBER

    def rect(r):
        return QtCore.QRectF(cx - r, cy - r, 2 * r, 2 * r)

    # dark glass disc + soft outer glow
    g = QtGui.QRadialGradient(cx, cy, R)
    g.setColorAt(0.0, QtGui.QColor(4, 18, 28, 235))
    g.setColorAt(0.80, QtGui.QColor(3, 14, 22, 215))
    g.setColorAt(1.0, QtGui.QColor(3, 14, 22, 0))
    p.setPen(QtCore.Qt.NoPen); p.setBrush(g); p.drawEllipse(rect(R))
    if active:
        halo = QtGui.QRadialGradient(cx, cy, R)
        halo.setColorAt(0.70, _c(main, 0.0)); halo.setColorAt(0.88, _c(main, 0.22 * glow)); halo.setColorAt(1.0, _c(main, 0.0))
        p.setBrush(halo); p.drawEllipse(rect(R))

    rot = t * 8.0 * speed  # degrees

    # 1. outer broken ring
    pen = QtGui.QPen(_c(main, 0.85 * base_a * glow), max(1.5, s * 0.008)); pen.setCapStyle(QtCore.Qt.FlatCap)
    for k in range(4):
        _arc(p, rect(R * 0.95), rot * 0.5 + k * 90 + 8, 66, pen)
    pen2 = QtGui.QPen(_c(ICE, 0.9 * base_a * glow), max(2.5, s * 0.016)); pen2.setCapStyle(QtCore.Qt.FlatCap)
    for k in range(4):
        _arc(p, rect(R * 0.95), rot * 0.5 + k * 90 + 74, 10, pen2)

    # 2. tick ring (counter-rotating)
    p.save(); p.translate(cx, cy); p.rotate(-rot * 0.35)
    n = 120
    for i in range(n):
        long = i % 10 == 0
        r1 = R * (0.80 if long else 0.84); r2 = R * 0.88
        a = 2 * math.pi * i / n
        lit = 1.0
        if state == "listening":
            lit = 0.5 + 0.5 * max(0.0, math.sin(a * 3 - t * 8))
        p.setPen(QtGui.QPen(_c(main, (0.95 if long else 0.55) * base_a * lit * min(glow, 1.6)), 1.2 if not long else 1.8))
        p.drawLine(QtCore.QPointF(r1 * math.cos(a), r1 * math.sin(a)), QtCore.QPointF(r2 * math.cos(a), r2 * math.sin(a)))
    p.restore()

    # 3. thick segmented band (the big light-blue arc bar)
    band_r = R * 0.68; band_w = R * 0.15
    segs = 36; span_total = 300.0
    start = -60 + rot * 0.25
    if state == "listening":
        lit_count = int(segs * min(1.0, 0.45 + level * 1.4 + 0.25 * (0.5 + 0.5 * math.sin(t * 5))))
    elif state == "thinking":
        lit_count = segs
    else:
        lit_count = int(segs * 0.72)
    for i in range(segs):
        a0 = start + i * span_total / segs
        on = i < lit_count
        if state == "thinking":
            phase = (t * 2.2) % 1.0
            on = abs(((i / segs) - phase + 0.5) % 1.0 - 0.5) < 0.18
        a = (0.62 if on else 0.14) * base_a * (glow if on else 1.0)
        pen = QtGui.QPen(_c(ICE if on else DEEP, a), band_w); pen.setCapStyle(QtCore.Qt.FlatCap)
        _arc(p, rect(band_r), a0, span_total / segs - 2.2, pen)
    # band edges
    p.setPen(QtGui.QPen(_c(main, 0.8 * base_a), 1.2)); p.setBrush(QtCore.Qt.NoBrush)
    p.drawEllipse(rect(band_r + band_w / 2 + 2)); p.drawEllipse(rect(band_r - band_w / 2 - 2))

    # 4. amber accent arc with dots (left side, like the reference)
    acc_r = R * 0.55
    pen = QtGui.QPen(_c(accent, 0.95 * base_a * min(glow, 1.4)), max(2.0, s * 0.012)); pen.setCapStyle(QtCore.Qt.FlatCap)
    wob = 6 * math.sin(t * 0.7) if not paused else 0
    _arc(p, rect(acc_r), 160 + wob, 50, pen)
    p.setPen(QtCore.Qt.NoPen); p.setBrush(_c(accent, 0.95 * base_a))
    for j in range(4):
        a = math.radians(-(265 + j * 6 + rot * 0.2))
        x = cx + R * 0.86 * math.cos(a); y = cy + R * 0.86 * math.sin(a)
        p.drawEllipse(QtCore.QPointF(x, y), s * 0.008, s * 0.008)

    # 5. inner rings
    p.setBrush(QtCore.Qt.NoBrush)
    p.setPen(QtGui.QPen(_c(main, 0.55 * base_a), 1.0)); p.drawEllipse(rect(R * 0.47))
    pen = QtGui.QPen(_c(main, 0.8 * base_a * glow), 2.0); pen.setCapStyle(QtCore.Qt.FlatCap)
    for k in range(3):
        _arc(p, rect(R * 0.43), -rot * 0.8 + k * 120, 80, pen)
    if state == "thinking":
        pen = QtGui.QPen(_c(ICE, 0.95), 3.0); pen.setCapStyle(QtCore.Qt.RoundCap)
        _arc(p, rect(R * 0.39), t * 360 % 360, 70, pen)

    # 6. center text
    f = T.font("display", max(9, int(s * 0.058)), bold=True, spacing=s * 0.004)
    p.setFont(f)
    tr = QtCore.QRectF(0, cy - s * 0.08, s, s * 0.16)
    if not paused:
        p.setPen(_c(CYAN, 0.55 * glow))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(tr.translated(dx, dy), QtCore.Qt.AlignCenter, "J.A.R.V.I.S.")
    p.setPen(QtGui.QColor(255, 255, 255, 240 if not paused else 140))
    p.drawText(tr, QtCore.Qt.AlignCenter, "J.A.R.V.I.S.")
    sub = {"idle": "ONLINE", "listening": "LISTENING", "thinking": "PROCESSING", "speaking": "RESPONDING", "paused": "PAUSED"}[state]
    f2 = T.font("display", max(7, int(s * 0.036)), bold=True, spacing=s * 0.008)
    p.setFont(f2); p.setPen(_c(accent if state == "listening" else main, 0.9 * base_a))
    p.drawText(QtCore.QRectF(0, cy + s * 0.07, s, s * 0.07), QtCore.Qt.AlignCenter, sub)

    # 7. caption under the disc (last thing heard / said)
    if caption and tag:
        # v5: what JARVIS heard, for ~2 s: a small tag (HEARD / IGNORED / DID YOU MEAN?) and up to two lines of words
        ft = T.font("display", max(7, int(s * 0.034)), bold=True, spacing=s * 0.006)
        f3 = T.font("body", max(11, int(s * 0.056)))
        box = QtCore.QRectF(4, s + 2, s - 8, max(24, cap_h))
        p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(3, 14, 22, 215))
        p.drawRoundedRect(box, 6, 6)
        tc = tag_color if tag_color is not None else AMBER
        p.setPen(QtGui.QPen(_c(tc, 0.9), 1.2)); p.setBrush(QtCore.Qt.NoBrush)
        p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        p.setFont(ft); p.setPen(_c(tc, 1.0))
        fmt = QtGui.QFontMetrics(ft)
        p.drawText(QtCore.QRectF(box.left() + 7, box.top() + 3, box.width() - 14, fmt.height()), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, tag)
        p.setFont(f3); p.setPen(_c(ICE, 0.97))
        fm = QtGui.QFontMetrics(f3)
        body = QtCore.QRectF(box.left() + 7, box.top() + 3 + fmt.height(), box.width() - 14, box.height() - fmt.height() - 5)
        lines_fit = max(1, int(body.height() // fm.lineSpacing()))
        words, out, cur = caption.split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if fm.horizontalAdvance(trial) <= body.width() or not cur:
                cur = trial
            else:
                out.append(cur); cur = w
        if cur: out.append(cur)
        if len(out) > lines_fit:
            out = out[:lines_fit]; out[-1] = fm.elidedText(out[-1] + " …", QtCore.Qt.ElideRight, int(body.width()))
        p.drawText(body, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, "\n".join(out))
    elif caption:
        f3 = T.font("body", max(11, int(s * 0.055)))
        p.setFont(f3)
        cap_rect = QtCore.QRectF(4, s + 2, s - 8, 24)
        p.setPen(QtCore.Qt.NoPen); p.setBrush(QtGui.QColor(3, 14, 22, 190))
        p.drawRoundedRect(cap_rect, 6, 6)
        p.setPen(_c(ICE, 0.95))
        fm = QtGui.QFontMetrics(f3)
        p.drawText(cap_rect, QtCore.Qt.AlignCenter, fm.elidedText(caption, QtCore.Qt.ElideRight, int(cap_rect.width() - 10)))


# v5: the "heard" strip under the HUD (tag, colour)
HEARD_TAGS = {"heard": ("HEARD", lambda: T.accent()), "ignored": ("IGNORED", QtGui.QColor(138, 160, 176)),
              "ask": ("DID YOU MEAN?", QtGui.QColor(255, 196, 77)), "cancel": ("CANCELLED", QtGui.QColor(255, 120, 120)),
              "asleep": ("ASLEEP", QtGui.QColor(138, 160, 176)), "typed": ("TYPED", lambda: T.light())}


class Overlay(QtWidgets.QWidget):
    """Default: a SOLID dark rounded window (no WA_TranslucentBackground, no layered window, no
    WindowTransparentForInput, which Qt implements as WS_EX_LAYERED on Windows). Some GPU/driver/DWM
    setups never draw layered windows, which is why v2's ring never appeared on some PCs.
    hud.translucent = true brings back the see-through ring as an opt-in."""

    def _flags(self):
        f = (QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool
             | QtCore.Qt.WindowDoesNotAcceptFocus)
        if self.translucent:
            f |= QtCore.Qt.WindowTransparentForInput
        return f

    def __init__(self, cfg):
        self.translucent = bool(cfg.get("translucent", False))
        super().__init__(None)
        self.cfg = cfg
        self.opaque = not self.translucent
        self.setWindowFlags(self._flags())
        self.setWindowTitle("J.A.R.V.I.S. HUD")
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.size_px = int(cfg.get("size", 240))
        self.resize(self.size_px, self.size_px + 62)   # v5: room for two lines of "heard" text
        self._apply_mode()
        self.state = "idle"; self.level = 0.0; self.caption = ""; self._caption_until = 0
        self.heard = ""; self.heard_tag = ""; self.heard_color = None; self._heard_until = 0
        self.heard_seconds = float(cfg.get("heard_seconds", 2.0))
        self.t0 = time.monotonic()
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self.update)
        self.fps_idle = int(cfg.get("fps_idle", 15))
        self._apply_fps()
        self.place()
        log.info("HUD created: %s mode, %dx%d", "translucent" if self.translucent else "solid", self.width(), self.height())

    def _apply_mode(self):
        if self.translucent:
            self.clearMask()
            self.setAutoFillBackground(False)
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.setWindowOpacity(float(self.cfg.get("opacity", 0.92)))
        else:
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, False)
            self.setAttribute(QtCore.Qt.WA_NoSystemBackground, False)
            pal = self.palette(); pal.setColor(QtGui.QPalette.Window, T.bg0())
            self.setPalette(pal); self.setAutoFillBackground(True)
            self._apply_mask()

    def _apply_mask(self):
        """Rounded corners on a solid window via a window region (SetWindowRgn, not a layered window)."""
        if self.translucent:
            return
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(self.rect()), 20, 20)
        self.setMask(QtGui.QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply_mask()

    def place(self):
        primary = QtGui.QGuiApplication.primaryScreen()
        if primary is None:
            log.warning("HUD: no primary screen; waiting for a display")
            return
        scr = primary.availableGeometry()
        mx, my = int(self.cfg.get("margin_x", 24)), int(self.cfg.get("margin_y", 64))
        corner = self.cfg.get("corner", "bottom-right")
        w, h = self.width(), self.height()
        x = scr.right() - w - mx if "right" in corner else scr.left() + mx
        y = scr.bottom() - h - my if "bottom" in corner else scr.top() + my
        # Never let a stale multi-monitor setting place the HUD off-screen.
        x = max(scr.left(), min(scr.right() - w + 1, x))
        y = max(scr.top(), min(scr.bottom() - h + 1, y))
        self.move(x, y)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.place()
        self.enforce_topmost()
        log.info("HUD shown at (%d,%d), %dx%d, primary screen %s", self.x(), self.y(), self.width(),self.height(),
                 QtGui.QGuiApplication.primaryScreen().name() if QtGui.QGuiApplication.primaryScreen() else "none")

    def hideEvent(self, ev):
        super().hideEvent(ev)
        log.info("HUD hidden")

    def enforce_topmost(self):
        """Re-assert HWND_TOPMOST without stealing focus (tray calls this every ~2 s)."""
        if not self.isVisible():
            return
        try:
            self.raise_()
            if os.name == "nt":
                import ctypes
                flags = 0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
                fn = ctypes.windll.user32.SetWindowPos
                fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint]
                fn.restype = ctypes.c_int
                ok = fn(ctypes.c_void_p(int(self.winId())), ctypes.c_void_p(-1), 0, 0, 0, 0, flags)
                if not ok: log.warning("HUD: SetWindowPos failed (Windows error %d)", ctypes.windll.kernel32.GetLastError())
        except Exception:
            log.exception("HUD: couldn't raise the overlay window")

    def _apply_fps(self):
        fps = self.fps_idle if self.state in ("idle", "paused") else 30
        if self.state == "paused": fps = 4
        self.timer.start(int(1000 / max(1, fps)))

    @QtCore.Slot(str)
    def set_state(self, state):
        if state in STATES and state != self.state:
            self.state = state; self._apply_fps(); self.update()

    @QtCore.Slot(float)
    def set_level(self, level):
        self.level = 0.7 * self.level + 0.3 * level

    @QtCore.Slot(str)
    def set_caption(self, text, seconds=6):
        self.caption = text or ""; self._caption_until = time.monotonic() + seconds; self.update()

    @QtCore.Slot(str, str)
    def set_heard(self, text, status="heard"):
        """v5: show what JARVIS heard for ~2 s (status: heard / ignored / ask / cancel)."""
        tag, col = HEARD_TAGS.get(status, HEARD_TAGS["heard"])
        self.heard = text or ""; self.heard_tag = tag; self.heard_color = col() if callable(col) else col
        secs = self.heard_seconds * (2.5 if status == "ask" else 1.0)
        self._heard_until = time.monotonic() + secs; self.update()

    def set_opaque(self, on):
        """Switch between the solid HUD (default) and the see-through one (hud.translucent)."""
        on = bool(on)
        if on == self.opaque: return
        was_visible = self.isVisible()
        if was_visible: self.hide()
        self.opaque = on; self.translucent = not on
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        self.setWindowFlags(self._flags())
        self.setWindowFlag(QtCore.Qt.WindowTransparentForInput, self.translucent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)          # recreates the native window without the layered style
        if on:
            self.setWindowOpacity(1.0)
        self._apply_mode()
        self.update()
        if was_visible: self.show(); self.enforce_topmost()
        log.info("HUD mode: %s", "solid" if on else "translucent")

    def check_paint(self):
        """Offscreen paint sanity check; switch to opaque if the HUD couldn't draw."""
        try:
            img = QtGui.QImage(self.size(), QtGui.QImage.Format_ARGB32); img.fill(QtCore.Qt.transparent)
            self.render(img)
            if img.isNull() or img.pixelColor(self.size_px // 2, self.size_px // 2).alpha() < 20:
                raise RuntimeError("HUD paint produced no visible pixels")
            return True
        except Exception:
            log.exception("HUD paint check failed; switching to opaque compatibility mode")
            self.set_opaque(True)
            return False

    def paintEvent(self, ev):
        if self.caption and time.monotonic() > self._caption_until:
            self.caption = ""
        p = QtGui.QPainter(self)
        if self.opaque:
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            bg = T.bg0()
            p.fillRect(self.rect(), bg)
            g = QtGui.QRadialGradient(self.size_px / 2, self.size_px / 2, self.size_px * 0.7)
            g.setColorAt(0, T.bg1()); g.setColorAt(1, bg)
            p.setPen(QtCore.Qt.NoPen); p.setBrush(g); p.drawRoundedRect(self.rect(), 20, 20)
            p.setBrush(QtCore.Qt.NoBrush)
            p.setPen(QtGui.QPen(T.deep(), 2))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 20, 20)
            # corner brackets in the theme colour
            pen = QtGui.QPen(T.main(), 2); p.setPen(pen); L = 16; r = self.rect().adjusted(3, 3, -4, -4)
            for (x, y, dx, dy) in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1), (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
                p.drawLine(x + dx * 10, y, x + dx * (10 + L), y); p.drawLine(x, y + dy * 10, x, y + dy * (10 + L))
        if self.heard and time.monotonic() < self._heard_until:
            paint_hud(p, self.size_px, time.monotonic() - self.t0, self.state, self.level, self.heard,
                      tag=self.heard_tag, tag_color=self.heard_color, cap_h=self.height() - self.size_px - 6)
        else:
            self.heard = ""
            paint_hud(p, self.size_px, time.monotonic() - self.t0, self.state, self.level, self.caption)
        p.end()


def render_preview(path, state="idle", size=480, t=1.3, level=0.6, caption="", bg=None):
    """Render the HUD to a PNG (used for the download page previews)."""
    img = QtGui.QImage(size, size + (60 if caption else 0), QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(QtGui.QColor(0, 0, 0, 0) if bg is None else bg)
    p = QtGui.QPainter(img)
    paint_hud(p, size, t, state, level, caption)
    p.end()
    img.save(path)
