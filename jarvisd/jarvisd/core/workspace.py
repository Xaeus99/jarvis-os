"""v4: the J.A.R.V.I.S. workspace — a resizable desktop window with an arc reactor, live panels,
a conversation log and a command box. Opened from the tray, Ctrl+Shift+J, or "Jarvis, open workspace"."""
from __future__ import annotations
import base64, glob, json, logging, math, os, random, re, sys, threading, time
from PySide6 import QtCore, QtGui, QtWidgets
from . import theme as T
from .hudart import paint_reactor, paint_frame, paint_ring_gauge, paint_weather_glyph, _c
from . import wsdata

log = logging.getLogger("jarvis.workspace")

def _now():
    return time.monotonic()


# ======================================================================
#  building blocks
# ======================================================================
class Panel(QtWidgets.QWidget):
    TOP = 34
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = title
        self.reveal = 1.0
        self.hot = False
        self.setAttribute(QtCore.Qt.WA_StyledBackground, False)
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setContentsMargins(14, self.TOP, 14, 12)
        self.lay.setSpacing(8)
        self.header_widgets = []

    def add_header_button(self, btn):
        btn.setParent(self); self.header_widgets.append(btn); self._place_header(); btn.show()

    def _place_header(self):
        x = self.width() - 12
        for b in reversed(self.header_widgets):
            x -= b.width(); b.move(x, 7); x -= 6

    def resizeEvent(self, ev):
        super().resizeEvent(ev); self._place_header()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        paint_frame(p, QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.title, self.reveal, self.hot)
        self.paint_content(p)
        p.end()

    def paint_content(self, p):
        pass


class HudButton(QtWidgets.QAbstractButton):
    """A small angular HUD button with a glyph or text."""
    def __init__(self, text="", glyph=None, parent=None, w=None, h=26, tip=""):
        super().__init__(parent)
        self.setText(text); self.glyph = glyph
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(tip)
        f = T.font("display", 10, bold=True, spacing=1.5)
        fm = QtGui.QFontMetrics(f)
        self.setFixedSize(w or max(h, fm.horizontalAdvance(text) + 22 if text else h), h)
        self._hover = False

    def enterEvent(self, e): self._hover = True; self.update()
    def leaveEvent(self, e): self._hover = False; self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked() or self.isDown()
        main = T.main()
        path = QtGui.QPainterPath(); c = 6
        path.moveTo(r.left() + c, r.top()); path.lineTo(r.right(), r.top()); path.lineTo(r.right(), r.bottom() - c)
        path.lineTo(r.right() - c, r.bottom()); path.lineTo(r.left(), r.bottom()); path.lineTo(r.left(), r.top() + c); path.closeSubpath()
        p.setBrush(_c(main, 0.30 if on else 0.16 if self._hover else 0.07))
        p.setPen(QtGui.QPen(_c(main, 0.9 if (self._hover or on) else 0.45), 1.0))
        p.drawPath(path)
        col = T.light() if (self._hover or on) else _c(T.light(), 0.8)
        if self.glyph:
            draw_glyph(p, r, self.glyph, col)
        else:
            p.setPen(col); p.setFont(T.font("display", 10, bold=True, spacing=1.5))
            p.drawText(r, QtCore.Qt.AlignCenter, self.text())
        p.end()


def draw_glyph(p, r, g, col):
    p.save(); p.setRenderHint(QtGui.QPainter.Antialiasing)
    cx, cy = r.center().x(), r.center().y(); s = min(r.width(), r.height()) * 0.32
    p.setPen(QtGui.QPen(col, 1.8, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)); p.setBrush(QtCore.Qt.NoBrush)
    def tri(x, y, w, left=False):
        pts = [QtCore.QPointF(x - w / 2, y - w * 0.6), QtCore.QPointF(x + w / 2, y), QtCore.QPointF(x - w / 2, y + w * 0.6)]
        if left:
            pts = [QtCore.QPointF(2 * x - q.x(), q.y()) for q in pts]
        p.setBrush(col); p.drawPolygon(QtGui.QPolygonF(pts)); p.setBrush(QtCore.Qt.NoBrush)
    if g == "play": tri(cx + 1, cy, s * 1.6)
    elif g == "pause":
        p.setBrush(col); p.setPen(QtCore.Qt.NoPen)
        p.drawRect(QtCore.QRectF(cx - s * 0.7, cy - s, s * 0.5, 2 * s)); p.drawRect(QtCore.QRectF(cx + s * 0.2, cy - s, s * 0.5, 2 * s))
    elif g == "next":
        tri(cx - s * 0.2, cy, s * 1.2); p.setBrush(col); p.setPen(QtCore.Qt.NoPen); p.drawRect(QtCore.QRectF(cx + s * 0.45, cy - s * 0.75, s * 0.3, s * 1.5))
    elif g == "prev":
        tri(cx + s * 0.2, cy, s * 1.2, left=True); p.setBrush(col); p.setPen(QtCore.Qt.NoPen); p.drawRect(QtCore.QRectF(cx - s * 0.75, cy - s * 0.75, s * 0.3, s * 1.5))
    elif g == "close":
        p.drawLine(QtCore.QPointF(cx - s * 0.7, cy - s * 0.7), QtCore.QPointF(cx + s * 0.7, cy + s * 0.7))
        p.drawLine(QtCore.QPointF(cx + s * 0.7, cy - s * 0.7), QtCore.QPointF(cx - s * 0.7, cy + s * 0.7))
    elif g == "min":
        p.drawLine(QtCore.QPointF(cx - s * 0.8, cy + s * 0.5), QtCore.QPointF(cx + s * 0.8, cy + s * 0.5))
    elif g == "max":
        p.drawRect(QtCore.QRectF(cx - s * 0.7, cy - s * 0.6, s * 1.4, s * 1.2))
    elif g == "mic":
        p.drawRoundedRect(QtCore.QRectF(cx - s * 0.38, cy - s * 0.95, s * 0.76, s * 1.3), s * 0.38, s * 0.38)
        p.drawArc(QtCore.QRectF(cx - s * 0.7, cy - s * 0.6, s * 1.4, s * 1.3), 200 * 16, 140 * 16)
        p.drawLine(QtCore.QPointF(cx, cy + s * 0.7), QtCore.QPointF(cx, cy + s * 1.0))
    elif g == "send":
        p.setBrush(col)
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(cx - s, cy - s * 0.85), QtCore.QPointF(cx + s, cy), QtCore.QPointF(cx - s, cy + s * 0.85), QtCore.QPointF(cx - s * 0.5, cy)]))
    elif g == "sound_on" or g == "sound_off":
        p.setBrush(col)
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(cx - s, cy - s * 0.35), QtCore.QPointF(cx - s * 0.5, cy - s * 0.35), QtCore.QPointF(cx, cy - s * 0.9),
                                       QtCore.QPointF(cx, cy + s * 0.9), QtCore.QPointF(cx - s * 0.5, cy + s * 0.35), QtCore.QPointF(cx - s, cy + s * 0.35)]))
        p.setBrush(QtCore.Qt.NoBrush)
        if g == "sound_on":
            p.drawArc(QtCore.QRectF(cx - s * 0.4, cy - s * 0.6, s * 1.0, s * 1.2), -50 * 16, 100 * 16)
            p.drawArc(QtCore.QRectF(cx - s * 0.5, cy - s * 0.95, s * 1.5, s * 1.9), -50 * 16, 100 * 16)
        else:
            p.drawLine(QtCore.QPointF(cx + s * 0.3, cy - s * 0.4), QtCore.QPointF(cx + s * 1.0, cy + s * 0.4))
            p.drawLine(QtCore.QPointF(cx + s * 1.0, cy - s * 0.4), QtCore.QPointF(cx + s * 0.3, cy + s * 0.4))
    elif g == "theme":
        p.setBrush(T.main()); p.setPen(QtGui.QPen(T.light(), 1.2)); p.drawEllipse(QtCore.QPointF(cx - s * 0.35, cy), s * 0.6, s * 0.6)
        p.setBrush(T.accent()); p.drawEllipse(QtCore.QPointF(cx + s * 0.45, cy), s * 0.6, s * 0.6)
    elif g == "folder":
        p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(cx - s, cy - s * 0.6), QtCore.QPointF(cx - s * 0.3, cy - s * 0.6), QtCore.QPointF(cx - s * 0.1, cy - s * 0.3),
                                        QtCore.QPointF(cx + s, cy - s * 0.3), QtCore.QPointF(cx + s, cy + s * 0.75), QtCore.QPointF(cx - s, cy + s * 0.75), QtCore.QPointF(cx - s, cy - s * 0.6)]))
    elif g == "refresh":
        p.drawArc(QtCore.QRectF(cx - s * 0.8, cy - s * 0.8, s * 1.6, s * 1.6), 30 * 16, 280 * 16)
        p.setBrush(col); p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(cx + s * 0.95, cy - s * 0.75), QtCore.QPointF(cx + s * 0.95, cy - s * 0.05), QtCore.QPointF(cx + s * 0.3, cy - s * 0.4)]))
    elif g == "edit":
        p.drawLine(QtCore.QPointF(cx - s * 0.8, cy + s * 0.8), QtCore.QPointF(cx + s * 0.6, cy - s * 0.6))
        p.drawLine(QtCore.QPointF(cx - s * 0.8, cy + s * 0.8), QtCore.QPointF(cx - s * 0.9, cy + s * 0.9))
        p.drawLine(QtCore.QPointF(cx + s * 0.35, cy - s * 0.85), QtCore.QPointF(cx + s * 0.85, cy - s * 0.35))
    elif g == "plus":
        p.drawLine(QtCore.QPointF(cx - s, cy), QtCore.QPointF(cx + s, cy)); p.drawLine(QtCore.QPointF(cx, cy - s), QtCore.QPointF(cx, cy + s))
    p.restore()


# ======================================================================
#  panels
# ======================================================================
class ClockPanel(Panel):
    def __init__(self, parent=None):
        super().__init__("Chronometer", parent)
        self.setMinimumHeight(150)
        t = QtCore.QTimer(self); t.timeout.connect(self.update); t.start(1000)
        self.focus_end = None; self.focus_total = 0

    def paint_content(self, p):
        r = QtCore.QRectF(self.rect()).adjusted(16, self.TOP, -16, -12)
        now = time.localtime()
        hh = time.strftime("%I:%M", now).lstrip("0"); ss = time.strftime("%S", now); ap = time.strftime("%p", now)
        big = T.font("display", min(56, r.height() * 0.42, r.width() * 0.2), bold=True, spacing=2)
        p.setFont(big); fm = QtGui.QFontMetricsF(big)
        tw = fm.horizontalAdvance(hh)
        y = r.top() + 2; h = fm.height()
        # glow pass
        for dx, dy, a in ((0, 0, 0.0),):
            pass
        p.setPen(_c(T.main(), 0.35))
        for d in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(QtCore.QRectF(r.left() + d[0], y + d[1], tw + 4, h), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, hh)
        p.setPen(QtGui.QColor(255, 255, 255))
        p.drawText(QtCore.QRectF(r.left(), y, tw + 4, h), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, hh)
        small = T.font("display", max(10, big.pixelSize() * 0.32), bold=True, spacing=1)
        p.setFont(small); p.setPen(T.main())
        p.drawText(QtCore.QRectF(r.left() + tw + 10, y + h * 0.18, 80, h * 0.4), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, ss)
        p.setPen(T.accent())
        p.drawText(QtCore.QRectF(r.left() + tw + 10, y + h * 0.52, 80, h * 0.4), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, ap)
        p.setFont(T.font("display", 12, spacing=2.2)); p.setPen(_c(T.light(), 0.85))
        date = time.strftime("%A", now).upper() + "  ·  " + time.strftime("%B", now).upper() + " " + str(now.tm_mday) + ", " + str(now.tm_year)
        p.drawText(QtCore.QRectF(r.left(), y + h + 2, r.width(), 18), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, date)
        # day progress bar or the focus timer
        by = y + h + 30
        if by + 22 > r.bottom() + 6:
            return
        if self.focus_end:
            left = max(0, self.focus_end - time.time())
            frac = 1 - left / max(1, self.focus_total)
            label = "FOCUS  %02d:%02d" % (left // 60, left % 60)
            col = T.accent()
        else:
            secs = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
            frac = secs / 86400.0
            label = "DAY  %d%%" % round(frac * 100)
            col = T.main()
        p.setFont(T.font("display", 9, bold=True, spacing=1.6)); p.setPen(_c(T.light(), 0.75))
        p.drawText(QtCore.QRectF(r.left(), by, r.width(), 12), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label)
        segs = 40; w = r.width(); sw = w / segs
        for i in range(segs):
            on = i < int(frac * segs)
            p.fillRect(QtCore.QRectF(r.left() + i * sw, by + 16, sw - 2, 6), _c(col if on else T.deep(), 0.9 if on else 0.35))


class WeatherPanel(Panel):
    def __init__(self, parent=None):
        super().__init__("Atmospherics", parent)
        self.setMinimumHeight(130)
        self.data = None; self.t0 = _now()
        t = QtCore.QTimer(self); t.timeout.connect(self.update); t.start(250)

    def set_data(self, d):
        self.data = d; self.update()

    def paint_content(self, p):
        r = QtCore.QRectF(self.rect()).adjusted(14, self.TOP, -14, -10)
        d = self.data
        if not d:
            p.setFont(T.font("body", 15)); p.setPen(_c(T.light(), 0.7))
            p.drawText(r, QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, "Fetching the weather…")
            return
        if d.get("error"):
            p.setFont(T.font("body", 15)); p.setPen(_c(T.light(), 0.7))
            p.drawText(r, QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, d["error"])
            return
        g = min(r.height() - 4, 110)
        f = T.font("display", min(44, r.height() * 0.40), bold=True)
        fm = QtGui.QFontMetricsF(f)
        block_h = fm.height() + 24
        top = r.top() + max(0, (r.height() - block_h) / 2) - 2
        paint_weather_glyph(p, QtCore.QRectF(r.left() + 4, r.top() + (r.height() - g) / 2, g, g), d.get("code"), _now() - self.t0)
        x = r.left() + g + 18
        p.setFont(f); p.setPen(QtGui.QColor(255, 255, 255))
        temp = "%d°" % round(d.get("temp", 0))
        p.drawText(QtCore.QRectF(x, top, fm.horizontalAdvance(temp) + 6, fm.height()), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, temp)
        tx = x + fm.horizontalAdvance(temp) + 16
        p.setFont(T.font("display", 11, bold=True, spacing=1.8)); p.setPen(T.light())
        fmc = QtGui.QFontMetrics(p.font())
        p.drawText(QtCore.QRectF(tx, top + fm.height() * 0.12, r.right() - tx, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fmc.elidedText((d.get("cond") or "").upper(), QtCore.Qt.ElideRight, int(max(10, r.right() - tx))))
        p.setFont(T.font("body", 15)); p.setPen(_c(T.light(), 0.8))
        p.drawText(QtCore.QRectF(tx, top + fm.height() * 0.12 + 18, r.right() - tx, 18), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   "H %d°  ·  L %d°" % (round(d.get("hi", 0)), round(d.get("lo", 0))))
        p.setFont(T.font("display", 9, bold=True, spacing=1.4)); p.setPen(T.main())
        city = (d.get("city") or "").split(",")[0].upper()
        line = "%s   WIND %d %s   RAIN %d%%" % (city, round(d.get("wind", 0)), d.get("wind_unit", "mph").upper(), round(d.get("rain") or 0))
        fml = QtGui.QFontMetrics(p.font())
        p.drawText(QtCore.QRectF(x, top + fm.height() + 6, r.right() - x, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fml.elidedText(line.strip(), QtCore.Qt.ElideRight, int(r.right() - x)))


class SystemPanel(Panel):
    def __init__(self, parent=None):
        super().__init__("Systems", parent)
        self.setMinimumHeight(200)
        self.s = {}
        self.down = [0.0] * 60; self.up = [0.0] * 60

    def set_stats(self, s):
        self.s = s or {}
        if s.get("net_down") is not None:
            self.down = self.down[1:] + [s["net_down"]]; self.up = self.up[1:] + [s["net_up"]]
        self.update()

    def paint_content(self, p):
        s = self.s
        r = QtCore.QRectF(self.rect()).adjusted(10, self.TOP, -10, -10)
        gh = min(r.width() / 4, max(60, r.height() - 58))
        gauges = [("CPU", s.get("cpu"), ""),
                  ("RAM", s.get("ram"), "%.1f / %.0f GB" % (s.get("ram_used_gb", 0), s.get("ram_total_gb", 0)) if s.get("ram_total_gb") else ""),
                  ("GPU", s.get("gpu"), ("%d°C" % s["gpu_temp"]) if s.get("gpu_temp") is not None else ("N/A" if s and s.get("gpu") is None else "")),
                  ("DISK", s.get("disk"), ("%s %.0f GB free" % (s.get("disk_name", ""), s.get("disk_free_gb", 0))) if s.get("disk_free_gb") is not None else "")]
        w = r.width() / 4
        for i, (lab, v, sub) in enumerate(gauges):
            paint_ring_gauge(p, QtCore.QRectF(r.left() + i * w, r.top(), w, gh + 16), v, lab, sub,
                             warn=90 if lab == "DISK" else 85)
        # network sparkline
        ny = r.top() + gh + 24; nh = r.bottom() - ny
        if nh < 24:
            return
        box = QtCore.QRectF(r.left() + 4, ny, r.width() - 8, nh)
        p.setPen(QtGui.QPen(_c(T.main(), 0.18), 1)); p.setBrush(QtCore.Qt.NoBrush)
        for k in range(1, 4):
            yy = box.top() + box.height() * k / 4
            p.drawLine(QtCore.QPointF(box.left(), yy), QtCore.QPointF(box.right(), yy))
        mx = max(max(self.down), max(self.up), 50_000)
        for series, col, fill in ((self.down, T.main(), True), (self.up, T.accent(), False)):
            path = QtGui.QPainterPath()
            for i, v in enumerate(series):
                x = box.left() + box.width() * i / (len(series) - 1); y = box.bottom() - (box.height() - 14) * v / mx
                (path.moveTo if i == 0 else path.lineTo)(x, y)
            if fill:
                fp = QtGui.QPainterPath(path); fp.lineTo(box.right(), box.bottom()); fp.lineTo(box.left(), box.bottom()); fp.closeSubpath()
                g = QtGui.QLinearGradient(box.topLeft(), box.bottomLeft()); g.setColorAt(0, _c(col, 0.35)); g.setColorAt(1, _c(col, 0.0))
                p.setPen(QtCore.Qt.NoPen); p.setBrush(g); p.drawPath(fp)
            p.setBrush(QtCore.Qt.NoBrush); p.setPen(QtGui.QPen(col, 1.6)); p.drawPath(path)
        p.setFont(T.font("display", 9, bold=True, spacing=1.4))
        p.setPen(T.light())
        p.drawText(QtCore.QRectF(box.left() + 4, box.top(), box.width(), 14), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   "NET  ▼ %s/s" % wsdata.human_bytes(s.get("net_down")))
        p.setPen(T.accent())
        p.drawText(QtCore.QRectF(box.left(), box.top(), box.width() - 4, 14), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                   "▲ %s/s" % wsdata.human_bytes(s.get("net_up")))


class ReactorWidget(QtWidgets.QWidget):
    clicked = QtCore.Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"; self.level = 0.0; self.boot = 1.0
        self.caption = ""; self._cap_until = 0
        self.t0 = _now()
        self.setMinimumSize(260, 260)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Click to talk to J.A.R.V.I.S.")
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self.update)
        self.fps(30)

    def fps(self, n):
        self.timer.start(int(1000 / max(1, n)))

    def set_state(self, s):
        self.state = s; self.update()

    def set_level(self, v):
        self.level = 0.6 * self.level + 0.4 * float(v)

    def set_caption(self, text, seconds=8):
        self.caption = text or ""; self._cap_until = _now() + seconds

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        r = QtCore.QRectF(self.rect())
        cap_h = 30
        rr = r.adjusted(0, 0, 0, -cap_h)
        if self.state != "listening":
            self.level *= 0.9
        paint_reactor(p, rr, _now() - self.t0, self.state, self.level, self.boot)
        if self.caption and _now() < self._cap_until:
            f = T.font("body", 17); p.setFont(f); fm = QtGui.QFontMetrics(f)
            text = fm.elidedText(self.caption, QtCore.Qt.ElideRight, int(r.width() - 40))
            tw = fm.horizontalAdvance(text) + 30
            box = QtCore.QRectF(r.center().x() - tw / 2, r.bottom() - cap_h + 2, tw, cap_h - 4)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            p.setPen(QtGui.QPen(_c(T.main(), 0.5), 1)); p.setBrush(_c(T.bg0(), 0.85))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(T.light()); p.drawText(box, QtCore.Qt.AlignCenter, text)
        p.end()


class ConvoPanel(Panel):
    WHO = {"you": "YOU", "jarvis": "J.A.R.V.I.S.", "tab": "TAB", "system": "SYSTEM", "heard": "HEARD · IGNORED"}
    def __init__(self, parent=None):
        super().__init__("History", parent)   # v5: every heard line and reply, saved daily in JARVIS-data\logs
        self.view = QtWidgets.QTextBrowser(self)
        self.view.setOpenExternalLinks(True)
        self.view.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.lay.addWidget(self.view)
        self.entries = []
        self.setMinimumHeight(150)
        self.restyle()

    def restyle(self):
        m = T.main()
        self.view.setStyleSheet(
            "QTextBrowser{background:transparent;color:%s;border:none;selection-background-color:%s;}"
            "QScrollBar:vertical{background:transparent;width:6px;margin:2px;}"
            "QScrollBar::handle:vertical{background:%s;border-radius:3px;min-height:24px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0px;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:none;}" % (
                T.hexa(T.light()), T.rgba(m, 120), T.rgba(m, 110)))
        self._render()

    def add(self, who, text, ts=None):
        text = (text or "").strip()
        if not text:
            return
        self.entries.append((ts or time.time(), who, text))
        self.entries = self.entries[-200:]
        self._render()

    def set_entries(self, entries):
        self.entries = list(entries)[-200:]; self._render()

    def _render(self):
        import html
        cols = {"you": T.hexa(T.accent()), "jarvis": T.hexa(T.main()), "tab": "#c9a6ff" if T.current() not in ("vibranium",) else "#5ce1ff",
                "system": "#8aa0b0", "heard": "#6c7a88"}
        rows = []
        for ts, who, text in self.entries[-120:]:
            c = cols.get(who, "#8aa0b0")
            t = time.strftime("%H:%M", time.localtime(ts))
            body = html.escape(text).replace("\n", "<br>")
            body = re.sub(r"(https?://[^\s<]+)", r'<a style="color:%s" href="\1">\1</a>' % T.hexa(T.light()), body)
            rows.append(
                '<table width="100%%" cellspacing="0" cellpadding="0" style="margin-bottom:9px"><tr>'
                '<td style="font-family:\'%s\';font-size:10px;font-weight:700;color:%s;">▌ %s'
                '<span style="color:#6c8698;font-weight:400">&nbsp;&nbsp;%s</span></td></tr>'
                '<tr><td style="font-family:\'%s\';font-size:16px;color:%s;padding-left:10px;padding-top:1px">%s</td></tr></table>'
                % (T.DISPLAY, c, self.WHO.get(who, who.upper()), t, T.BODY,
                   "#7d8b99" if who == "heard" else ("#e9f8ff" if who != "you" else "#ffffff"),
                   ("<i>%s</i>" % body) if who == "heard" else body))
        if not rows:
            rows.append('<p style="font-family:\'%s\';font-size:15px;color:#6c8698">Nothing yet, sir. Say "Jarvis" or type below.</p>' % T.BODY)
        self.view.setHtml("".join(rows))
        sb = self.view.verticalScrollBar(); sb.setValue(sb.maximum())


class TabScreenPanel(Panel):
    """v5 "Tab's screen": a live activity feed of what Tab is doing for him (not video).
    Latest line types itself in over a scanline, an optional thumbnail (click to enlarge), an optional
    progress bar and step name, and the last ~10 steps. "Standing by" after 2 quiet minutes. Collapsible."""
    IDLE_S = 120
    def __init__(self, parent=None):
        super().__init__("Tab's screen", parent)
        self.items = []               # (ts, text, step, progress)
        self.image = None             # QImage
        self.latest = None
        self.t_line = 0.0
        self.collapsed = False
        self.full_h = None
        self.b_fold = HudButton(text="–", w=26, h=22, tip="Collapse / expand")
        self.b_fold.clicked.connect(self.toggle)
        self.add_header_button(self.b_fold)
        self.setMinimumHeight(190)
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self.update); self.timer.start(60)

    def toggle(self):
        self.collapsed = not self.collapsed
        self.b_fold.setText("+" if self.collapsed else "–")
        if self.collapsed:
            self.setMinimumHeight(self.TOP + 4); self.setMaximumHeight(self.TOP + 4)
        else:
            self.setMinimumHeight(190); self.setMaximumHeight(16777215)
        self.update()

    def add(self, act):
        """act: {text, step, progress, image(bytes|None), ts}"""
        text = (act.get("text") or "").strip()
        if not text and not act.get("image"):
            return
        ts = act.get("ts") or time.time()
        self.latest = {"text": text, "step": act.get("step") or "", "progress": act.get("progress"), "ts": time.time()}
        self.t_line = time.time()
        self.items.append((ts, text, act.get("step") or "", act.get("progress")))
        self.items = self.items[-10:]
        if act.get("image"):
            img = QtGui.QImage.fromData(act["image"])
            if not img.isNull():
                self.image = img
                self.gallery = (getattr(self, "gallery", []) + [img])[-12:]    # v5.1: arrows step through these
        self.update()

    def idle(self):
        return self.latest is None or time.time() - self.latest["ts"] > self.IDLE_S

    def _thumb_rect(self):
        if self.image is None or self.idle():
            return None
        r = QtCore.QRectF(self.rect()).adjusted(14, self.TOP + 2, -14, -12)
        h = min(max(r.height() * 0.62, 86.0), 124.0) - 8; w = h * self.image.width() / max(1, self.image.height())
        w = min(w, r.width() * 0.45); h = w * self.image.height() / max(1, self.image.width())
        return QtCore.QRectF(r.right() - w - 4, r.top() + 4, w, h)

    def mousePressEvent(self, e):
        tr = self._thumb_rect()
        if tr is not None and tr.contains(e.position()):
            # v5.1: full screen, fit to the screen; Esc / click / X closes, arrows step through recent images
            try:
                from .progress import open_viewer
            except ImportError:      # JARVIS OS: no viewer module; the panel just doesn't zoom
                return
            gal = getattr(self, "gallery", None) or [self.image]
            self.viewer = open_viewer(gal, len(gal) - 1, "Tab's screen", None)
            return
        super().mousePressEvent(e)

    def paint_content(self, p):
        if self.collapsed:
            if not self.idle():
                p.setPen(_c(T.light(), 0.85)); p.setFont(T.font("body", 13))
                fm = QtGui.QFontMetrics(p.font())
                p.drawText(QtCore.QRectF(130, 8, self.width() - 180, 20), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                           fm.elidedText(self.latest["text"], QtCore.Qt.ElideRight, self.width() - 190))
            return
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(14, self.TOP + 2, -14, -12)
        main, light = T.main(), T.light()
        now = time.time()
        # screen area with scanlines
        scr = QtCore.QRectF(r.left(), r.top(), r.width(), min(max(r.height() * 0.62, 86.0), 124.0))
        p.setPen(QtGui.QPen(_c(main, 0.45), 1)); p.setBrush(_c(T.bg0(), 0.65)); p.drawRoundedRect(scr, 6, 6)
        p.save(); p.setClipRect(scr)
        p.setPen(QtGui.QPen(_c(main, 0.07), 1))
        y = scr.top()
        while y < scr.bottom():
            p.drawLine(QtCore.QPointF(scr.left(), y), QtCore.QPointF(scr.right(), y)); y += 3
        sweep = scr.top() + ((now * 40) % max(1.0, scr.height()))
        g = QtGui.QLinearGradient(0, sweep - 18, 0, sweep)
        g.setColorAt(0, _c(main, 0.0)); g.setColorAt(1, _c(main, 0.22))
        p.fillRect(QtCore.QRectF(scr.left(), sweep - 18, scr.width(), 18), g)
        tr = self._thumb_rect()
        text_w = scr.width() - 20 - ((tr.width() + 10) if tr is not None else 0)
        if self.idle():
            p.setPen(_c(light, 0.6)); p.setFont(T.font("display", 12, bold=True, spacing=3))
            dots = "." * (int(now * 2) % 4)
            p.drawText(scr, QtCore.Qt.AlignCenter, "STANDING BY" + dots)
        else:
            if tr is not None:
                p.drawImage(tr, self.image); p.setPen(QtGui.QPen(_c(light, 0.8), 1)); p.setBrush(QtCore.Qt.NoBrush); p.drawRect(tr)
            lt = self.latest
            if lt["step"]:
                p.setPen(T.accent()); p.setFont(T.font("display", 9, bold=True, spacing=2))
                p.drawText(QtCore.QRectF(scr.left() + 10, scr.top() + 7, text_w, 14), QtCore.Qt.AlignLeft, lt["step"].upper())
            n = int((now - self.t_line) * 45)          # typing animation
            shown = lt["text"][:n]
            cursor = "▌" if (n < len(lt["text"]) or int(now * 2) % 2) else " "
            p.setPen(QtGui.QColor("#f2ecff")); f = T.font("body", 14); p.setFont(f)
            top = scr.top() + (24 if lt["step"] else 8)
            p.drawText(QtCore.QRectF(scr.left() + 10, top, text_w, scr.bottom() - 20 - top),
                       QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap, shown + cursor)
            if lt["progress"] is not None:
                bar = QtCore.QRectF(scr.left() + 10, scr.bottom() - 12, text_w, 5)
                p.setPen(QtCore.Qt.NoPen); p.setBrush(_c(main, 0.2)); p.drawRoundedRect(bar, 2, 2)
                p.setBrush(main); p.drawRoundedRect(QtCore.QRectF(bar.left(), bar.top(), bar.width() * lt["progress"] / 100.0, bar.height()), 2, 2)
                p.setPen(_c(light, 0.9)); p.setFont(T.font("display", 8, bold=True))
                p.drawText(QtCore.QRectF(bar.right() - 40, bar.top() - 14, 40, 12), QtCore.Qt.AlignRight, f"{lt['progress']}%")
        p.restore()
        # history (newest at the bottom)
        hy = scr.bottom() + 8
        p.setFont(T.font("body", 13)); fm = QtGui.QFontMetrics(p.font()); lh = fm.lineSpacing()
        room = int((r.bottom() - hy) // lh)
        rows = self.items[:-1][-room:] if room > 0 else []
        for ts, text, step, prog in rows:
            p.setPen(_c(light, 0.45)); p.drawText(QtCore.QRectF(r.left(), hy, 48, lh), QtCore.Qt.AlignLeft, time.strftime("%H:%M", time.localtime(ts)))
            p.setPen(QtGui.QColor("#b9b0cc"))
            p.drawText(QtCore.QRectF(r.left() + 50, hy, r.width() - 50, lh), QtCore.Qt.AlignLeft,
                       fm.elidedText(("✓ " if prog == 100 else "· ") + text, QtCore.Qt.ElideRight, int(r.width() - 52)))
            hy += lh


class CommandBar(QtWidgets.QWidget):
    submitted = QtCore.Signal(str)
    talk = QtCore.Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(8)
        self.mic = HudButton(glyph="mic", w=44, h=44, tip="Talk (same as saying \"Jarvis\")")
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText("Type a command, sir…   e.g.  status report  ·  open spotify  ·  what's the weather")
        self.edit.setMinimumHeight(44)
        self.send = HudButton(glyph="send", w=44, h=44, tip="Send (Enter)")
        lay.addWidget(self.mic); lay.addWidget(self.edit, 1); lay.addWidget(self.send)
        self.edit.returnPressed.connect(self._go); self.send.clicked.connect(self._go); self.mic.clicked.connect(self.talk.emit)
        self.history = []; self.hi = 0
        self.edit.installEventFilter(self)
        self.restyle()

    def restyle(self):
        m = T.main()
        self.edit.setStyleSheet(
            "QLineEdit{background:%s;color:#ffffff;border:1px solid %s;border-radius:2px;padding:0 14px;"
            "font-family:'%s';font-size:17px;selection-background-color:%s;}"
            "QLineEdit:focus{border:1px solid %s;background:%s;}" % (
                T.rgba(T.bg0(), 235), T.rgba(m, 110), T.BODY, T.rgba(m, 140), T.rgba(m, 230), T.rgba(T.bg1(), 245)))
        pal = self.edit.palette(); pal.setColor(QtGui.QPalette.PlaceholderText, _c(T.light(), 0.42)); self.edit.setPalette(pal)

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QtCore.QEvent.KeyPress and self.history:
            if ev.key() == QtCore.Qt.Key_Up:
                self.hi = max(0, self.hi - 1); self.edit.setText(self.history[self.hi]); return True
            if ev.key() == QtCore.Qt.Key_Down:
                self.hi = min(len(self.history), self.hi + 1)
                self.edit.setText(self.history[self.hi] if self.hi < len(self.history) else ""); return True
        return False

    def _go(self):
        t = self.edit.text().strip()
        if not t:
            return
        self.history = (self.history + [t])[-50:]; self.hi = len(self.history)
        self.edit.clear()
        self.submitted.emit(t)


class MediaPanel(Panel):
    command = QtCore.Signal(str)
    def __init__(self, parent=None):
        super().__init__("Now playing", parent)
        self.setMinimumHeight(150)
        self.d = {}
        self.t0 = _now()
        row = QtWidgets.QHBoxLayout(); row.setSpacing(8); row.addStretch(1)
        self.b_prev = HudButton(glyph="prev", w=40, h=32, tip="Previous")
        self.b_play = HudButton(glyph="play", w=52, h=32, tip="Play / pause")
        self.b_next = HudButton(glyph="next", w=40, h=32, tip="Next")
        for b, a in ((self.b_prev, "previous"), (self.b_play, "play_pause"), (self.b_next, "next")):
            row.addWidget(b); b.clicked.connect(lambda _=False, a=a: self.command.emit(a))
        row.addStretch(1)
        self.lay.addStretch(1); self.lay.addLayout(row)
        self.anim = QtCore.QTimer(self); self.anim.timeout.connect(self.update); self.anim.start(80)

    def set_data(self, d):
        self.d = d or {}
        self.b_play.glyph = "pause" if self.d.get("playing") else "play"; self.b_play.update()

    def paint_content(self, p):
        r = QtCore.QRectF(self.rect()).adjusted(16, self.TOP, -16, -50)
        d = self.d; t = _now() - self.t0
        # equalizer
        eq_w = min(70, r.width() * 0.22)
        bars = 7; bw = eq_w / bars
        for i in range(bars):
            if d.get("playing"):
                h = 0.25 + 0.75 * abs(math.sin(t * (2.1 + i * 0.37) + i * 1.3) * math.sin(t * (1.3 + i * 0.21)))
            else:
                h = 0.12
            hh = (r.height() - 4) * h
            g = QtGui.QLinearGradient(0, r.bottom(), 0, r.bottom() - hh)
            g.setColorAt(0, _c(T.main(), 0.9)); g.setColorAt(1, _c(T.light(), 0.95))
            p.fillRect(QtCore.QRectF(r.left() + i * bw, r.bottom() - hh, bw - 3, hh), g)
        x = r.left() + eq_w + 14
        title = d.get("title") or ("Nothing playing" if d is not None else "")
        f = T.font("body", 20, bold=True); p.setFont(f); fm = QtGui.QFontMetrics(f)
        p.setPen(QtGui.QColor(255, 255, 255))
        p.drawText(QtCore.QRectF(x, r.top(), r.right() - x, 26), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fm.elidedText(title, QtCore.Qt.ElideRight, int(r.right() - x)))
        f2 = T.font("body", 15); p.setFont(f2); fm2 = QtGui.QFontMetrics(f2); p.setPen(_c(T.light(), 0.8))
        sub = d.get("artist") or ("Start some music and I'll show it here." if not d.get("title") else "")
        p.drawText(QtCore.QRectF(x, r.top() + 26, r.right() - x, 20), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fm2.elidedText(sub, QtCore.Qt.ElideRight, int(r.right() - x)))
        if d.get("app"):
            p.setFont(T.font("display", 9, bold=True, spacing=1.6)); p.setPen(T.accent())
            p.drawText(QtCore.QRectF(x, r.top() + 48, r.right() - x, 14), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                       ("▶ " if d.get("playing") else "❚❚ ") + d["app"].upper())
        if d.get("duration"):
            frac = max(0.0, min(1.0, (d.get("position") or 0) / max(1.0, d["duration"])))
            yy = r.top() + 68
            if yy < r.bottom():
                p.fillRect(QtCore.QRectF(x, yy, r.right() - x, 3), _c(T.deep(), 0.6))
                p.fillRect(QtCore.QRectF(x, yy, (r.right() - x) * frac, 3), T.main())


DEFAULT_LAUNCH = [   # JARVIS OS public build: generic tiles (he adds his own games with the + tile)
    {"name": "Brave", "target": "brave"}, {"name": "Spotify", "target": "spotify"}, {"name": "Discord", "target": "discord"},
    {"name": "Steam", "target": "steam"}, {"name": "OBS", "target": "obs"}, {"name": "YouTube", "target": "https://www.youtube.com"},
    {"name": "Files", "target": "files"}, {"name": "Settings", "target": "settings"},
]

class LaunchTile(QtWidgets.QAbstractButton):
    remove = QtCore.Signal(object)
    rename = QtCore.Signal(object)
    move_by = QtCore.Signal(object, int)
    def __init__(self, entry, icon=None, add=False, parent=None):
        super().__init__(parent)
        self.entry, self.icon_, self.add = entry, icon, add
        self.editing = False; self._hover = False
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setMinimumSize(74, 74)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setToolTip("Add an app or website" if add else "%s\n%s" % (entry.get("name"), entry.get("target")))
        self.setContextMenuPolicy(QtCore.Qt.DefaultContextMenu)

    def enterEvent(self, e): self._hover = True; self.update()
    def leaveEvent(self, e): self._hover = False; self.update()

    def contextMenuEvent(self, e):
        if self.add:
            return
        m = QtWidgets.QMenu(self)
        m.addAction("Rename…").triggered.connect(lambda: self.rename.emit(self))
        m.addAction("Move left").triggered.connect(lambda: self.move_by.emit(self, -1))
        m.addAction("Move right").triggered.connect(lambda: self.move_by.emit(self, 1))
        m.addSeparator()
        m.addAction("Remove").triggered.connect(lambda: self.remove.emit(self))
        m.exec(e.globalPos())

    def paintEvent(self, ev):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(2, 2, -2, -2)
        main = T.main(); hov = self._hover or self.isDown()
        # hexagon-ish tile
        c = 10
        path = QtGui.QPainterPath()
        path.moveTo(r.left() + c, r.top()); path.lineTo(r.right() - c, r.top()); path.lineTo(r.right(), r.top() + c)
        path.lineTo(r.right(), r.bottom() - c); path.lineTo(r.right() - c, r.bottom()); path.lineTo(r.left() + c, r.bottom())
        path.lineTo(r.left(), r.bottom() - c); path.lineTo(r.left(), r.top() + c); path.closeSubpath()
        g = QtGui.QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, _c(main, 0.20 if hov else 0.10)); g.setColorAt(1, _c(main, 0.06 if hov else 0.02))
        p.setBrush(g)
        pen = QtGui.QPen(_c(main, 0.95 if hov else 0.40), 1.2)
        if self.add or self.editing:
            pen.setStyle(QtCore.Qt.DashLine)
        p.setPen(pen); p.drawPath(path)
        ic = min(r.width(), r.height() - 20) * 0.46
        icr = QtCore.QRectF(r.center().x() - ic / 2, r.top() + (r.height() - 20 - ic) / 2 + 4, ic, ic)
        if self.add:
            draw_glyph(p, icr, "plus", T.light())
            label = "ADD"
        else:
            label = (self.entry.get("name") or "?").upper()
            if self.icon_ is not None and not self.icon_.isNull():
                self.icon_.paint(p, icr.toRect())
            else:
                # monogram in a ring
                p.setPen(QtGui.QPen(_c(main, 0.9), 1.6)); p.setBrush(_c(main, 0.12))
                p.drawEllipse(icr)
                p.setFont(T.font("display", ic * 0.38, bold=True)); p.setPen(T.light())
                mono = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", self.entry.get("name") or "?")[:2]).upper() or "?"
                p.drawText(icr, QtCore.Qt.AlignCenter, mono)
        p.setFont(T.font("display", 8.5, bold=True, spacing=1.2)); p.setPen(_c(T.light(), 0.95 if hov else 0.75))
        fm = QtGui.QFontMetrics(p.font())
        p.drawText(QtCore.QRectF(r.left() + 2, r.bottom() - 20, r.width() - 4, 16), QtCore.Qt.AlignCenter,
                   fm.elidedText(label, QtCore.Qt.ElideRight, int(r.width() - 6)))
        if self.editing and not self.add:
            p.setPen(QtGui.QPen(T.accent(), 1.6)); p.setBrush(_c(T.bg0(), 0.9))
            p.drawEllipse(QtCore.QPointF(r.right() - 9, r.top() + 9), 7, 7)
            draw_glyph(p, QtCore.QRectF(r.right() - 16, r.top() + 2, 14, 14), "close", T.accent())
        p.end()


class LaunchPanel(Panel):
    launch = QtCore.Signal(dict)
    changed = QtCore.Signal(list)
    def __init__(self, entries, icon_for, parent=None):
        super().__init__("Quick launch", parent)
        self.entries = [dict(e) for e in (entries or DEFAULT_LAUNCH)]
        self.icon_for = icon_for
        self.editing = False
        self.setAcceptDrops(True)
        self.grid_host = QtWidgets.QWidget(); self.grid = QtWidgets.QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0); self.grid.setSpacing(8)
        self.lay.addWidget(self.grid_host, 1)
        self.b_edit = HudButton("EDIT", h=22, tip="Add, remove or rename tiles (you can also drop an app or shortcut here)")
        self.b_edit.setCheckable(True); self.b_edit.toggled.connect(self.set_editing)
        self.add_header_button(self.b_edit)
        self.setMinimumHeight(190)
        self._icons = {}
        self.rebuild()

    def cols(self):
        return max(3, min(6, int(self.width() / 96))) if self.width() > 0 else 4

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if getattr(self, "_last_cols", None) != self.cols():
            self.rebuild()

    def rebuild(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w: w.deleteLater()
        cols = self.cols(); self._last_cols = cols
        items = list(self.entries) + ([None] if self.editing else [])
        for i, e in enumerate(items):
            if e is None:
                t = LaunchTile({}, add=True); t.clicked.connect(self.add_dialog)
            else:
                key = (e.get("target"), e.get("name"))
                if key not in self._icons:
                    try: self._icons[key] = self.icon_for(e)
                    except Exception: self._icons[key] = None
                t = LaunchTile(e, self._icons[key])
                t.editing = self.editing
                t.clicked.connect(lambda _=False, t=t: self._tile_clicked(t))
                t.remove.connect(self._remove); t.rename.connect(self._rename); t.move_by.connect(self._move)
            self.grid.addWidget(t, i // cols, i % cols)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)

    def _tile_clicked(self, t):
        if self.editing:
            self._remove(t)
        else:
            self.launch.emit(t.entry)

    def set_editing(self, on):
        self.editing = bool(on); self.hot = self.editing; self.rebuild(); self.update()

    def _save(self):
        self.changed.emit([dict(e) for e in self.entries])

    def _remove(self, t):
        if t.entry in self.entries:
            self.entries.remove(t.entry); self._save(); self.rebuild()

    def _rename(self, t):
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename tile", "Name:", text=t.entry.get("name", ""))
        if ok and name.strip():
            t.entry["name"] = name.strip()[:24]; self._save(); self.rebuild()

    def _move(self, t, d):
        if t.entry not in self.entries:
            return
        i = self.entries.index(t.entry); j = max(0, min(len(self.entries) - 1, i + d))
        self.entries.insert(j, self.entries.pop(i)); self._save(); self.rebuild()

    def add_dialog(self):
        dlg = AddTileDialog(self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.result_entry:
            self.entries.append(dlg.result_entry); self._save(); self.rebuild()

    def add_target(self, target, name=None):
        name = name or os.path.splitext(os.path.basename(target.rstrip("/\\")))[0] or target
        self.entries.append({"name": name[:24], "target": target}); self._save(); self.rebuild()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            self.add_target(u.toLocalFile() if u.isLocalFile() else u.toString())


class AddTileDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add to quick launch")
        self.result_entry = None
        f = QtWidgets.QFormLayout(self)
        self.name = QtWidgets.QLineEdit(); self.target = QtWidgets.QLineEdit()
        self.target.setPlaceholderText(r"an app name (spotify), a website, or C:\path\to\game.exe" if os.name == "nt" else
                                       "an app name (spotify), a website, steam://rungameid/…, or /path/to/app")
        browse = QtWidgets.QPushButton("Browse…"); browse.clicked.connect(self._browse)
        row = QtWidgets.QHBoxLayout(); row.addWidget(self.target, 1); row.addWidget(browse)
        f.addRow("Name", self.name); f.addRow("Opens", row)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject); f.addRow(bb)
        self.setMinimumWidth(460)

    def _browse(self):
        start = os.path.join(os.environ.get("ProgramData", "C:\\ProgramData"), "Microsoft", "Windows", "Start Menu", "Programs")
        if os.name != "nt":
            start = "/usr/share/applications"
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Pick an app or shortcut", start, "Apps and shortcuts (*.exe *.lnk *.url *.bat);;All files (*)" if os.name == "nt" else "Apps and shortcuts (*.desktop *.AppImage *.sh);;All files (*)")
        if path:
            self.target.setText(os.path.normpath(path))
            if not self.name.text():
                self.name.setText(os.path.splitext(os.path.basename(path))[0])

    def _ok(self):
        t = self.target.text().strip()
        if not t:
            return
        self.result_entry = {"name": (self.name.text().strip() or os.path.splitext(os.path.basename(t))[0] or t)[:24], "target": t}
        self.accept()


class ClipTile(QtWidgets.QAbstractButton):
    def __init__(self, path, mtime, size, parent=None):
        super().__init__(parent)
        self.path, self.mtime, self.size_b = path, mtime, size
        self.pix = None; self._hover = False
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(path)
        self.setMinimumSize(120, 82)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def enterEvent(self, e): self._hover = True; self.update()
    def leaveEvent(self, e): self._hover = False; self.update()

    def set_thumb(self, path_or_img):
        img = QtGui.QImage(path_or_img) if isinstance(path_or_img, str) else path_or_img
        if img is not None and not img.isNull():
            self.pix = QtGui.QPixmap.fromImage(img); self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing); p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QtGui.QPainterPath(); clip.addRoundedRect(r, 4, 4)
        p.setClipPath(clip)
        p.fillRect(r, T.bg1())
        if self.pix:
            pm = self.pix.scaled(r.size().toSize(), QtCore.Qt.KeepAspectRatioByExpanding, QtCore.Qt.SmoothTransformation)
            p.drawPixmap(QtCore.QPointF(r.center().x() - pm.width() / 2, r.center().y() - pm.height() / 2), pm)
        else:
            p.setPen(QtGui.QPen(_c(T.main(), 0.2), 1))
            for k in range(0, int(r.width() + r.height()), 10):
                p.drawLine(QtCore.QPointF(r.left() + k, r.top()), QtCore.QPointF(r.left() + k - r.height(), r.bottom()))
        g = QtGui.QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0.45, QtGui.QColor(0, 0, 0, 0)); g.setColorAt(1.0, QtGui.QColor(0, 0, 0, 200))
        p.fillRect(r, g)
        if self._hover or not self.pix:
            p.setPen(QtCore.Qt.NoPen); p.setBrush(_c(T.bg0(), 0.7))
            p.drawEllipse(r.center(), 15, 15)
            draw_glyph(p, QtCore.QRectF(r.center().x() - 13, r.center().y() - 13, 28, 26), "play", T.light())
        p.setClipping(False)
        p.setPen(QtGui.QPen(_c(T.main(), 0.95 if self._hover else 0.35), 1.2)); p.setBrush(QtCore.Qt.NoBrush)
        p.drawRoundedRect(r, 4, 4)
        p.setFont(T.font("body", 13, bold=True)); p.setPen(QtGui.QColor(255, 255, 255))
        fm = QtGui.QFontMetrics(p.font())
        name = os.path.splitext(os.path.basename(self.path))[0]
        p.drawText(QtCore.QRectF(r.left() + 7, r.bottom() - 34, r.width() - 14, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fm.elidedText(name, QtCore.Qt.ElideRight, int(r.width() - 14)))
        p.setFont(T.font("display", 8, bold=True, spacing=1.2)); p.setPen(T.light())
        p.drawText(QtCore.QRectF(r.left() + 7, r.bottom() - 18, r.width() - 14, 14), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   wsdata.human_age(self.mtime).upper() + "  ·  " + wsdata.human_bytes(self.size_b))
        p.end()


class ClipsPanel(Panel):
    open_path = QtCore.Signal(str)
    refresh = QtCore.Signal()
    open_folder = QtCore.Signal()
    def __init__(self, parent=None):
        super().__init__("Recent clips", parent)
        self.host = QtWidgets.QWidget(); self.grid = QtWidgets.QGridLayout(self.host)
        self.grid.setContentsMargins(0, 0, 0, 0); self.grid.setSpacing(8)
        self.lay.addWidget(self.host, 1)
        self.empty = QtWidgets.QLabel("No clips yet, sir. Say “clip that” in a game and it'll show up here.")
        self.empty.setWordWrap(True); self.empty.setAlignment(QtCore.Qt.AlignCenter)
        self.lay.addWidget(self.empty); self.empty.hide()
        b1 = HudButton(glyph="refresh", w=26, h=22, tip="Refresh"); b1.clicked.connect(self.refresh.emit)
        b2 = HudButton(glyph="folder", w=26, h=22, tip="Open the clips folder"); b2.clicked.connect(self.open_folder.emit)
        self.add_header_button(b2); self.add_header_button(b1)
        self.tiles = {}
        self.setMinimumHeight(200)
        self.restyle()

    def restyle(self):
        self.empty.setStyleSheet("color:%s;font-family:'%s';font-size:15px;" % (T.rgba(T.light(), 170), T.BODY))

    def set_clips(self, clips):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w: w.deleteLater()
        self.tiles = {}
        clips = list(clips or [])[:6]
        self.empty.setVisible(not clips); self.host.setVisible(bool(clips))
        for i, (path, mtime, size) in enumerate(clips):
            t = ClipTile(path, mtime, size)
            t.clicked.connect(lambda _=False, p=path: self.open_path.emit(p))
            self.grid.addWidget(t, i // 2, i % 2)
            self.tiles[path] = t

    def set_thumb(self, path, thumb):
        t = self.tiles.get(path)
        if t and thumb:
            t.set_thumb(thumb)


# ======================================================================
#  top bar, boot sequence, background
# ======================================================================
class AudioCombo(QtWidgets.QComboBox):
    """Output-device picker. The list is read fresh each time it opens (headsets come and go)."""
    DEFAULT = "System default"
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setMinimumWidth(190); self.setMaximumWidth(280); self.setFixedHeight(28)
        self.addItem(self.DEFAULT)
        self.activated.connect(self._picked)
        self._busy = False

    def showPopup(self):
        names = []
        try:
            names = list((getattr(self.win, "audio_devices", None) or (lambda: []))())
        except Exception:
            names = []
        cur = self.currentText()
        self._busy = True
        self.clear(); self.addItem(self.DEFAULT); self.addItems(names)
        i = self.findText(cur)
        self.setCurrentIndex(max(0, i))
        self._busy = False
        super().showPopup()

    def set_current(self, name):
        from .audioout import is_default
        self._busy = True
        if is_default(name):
            self.setCurrentIndex(0)
        else:
            i = self.findText(name)
            if i < 0:
                self.addItem(name); i = self.count() - 1
            self.setCurrentIndex(i)
        self._busy = False

    def _picked(self, i):
        if self._busy:
            return
        name = self.itemText(i)
        cb = getattr(self.win, "on_audio", None)
        if cb:
            cb("system default" if name == self.DEFAULT else name)

    def restyle(self):
        m = T.main()
        self.setStyleSheet(
            "QComboBox{color:#e9f8ff;background:%s;border:1px solid %s;border-radius:6px;padding:2px 8px;font-family:'%s';font-size:14px;}"
            "QComboBox::drop-down{border:none;width:18px;}"
            "QComboBox QAbstractItemView{background:%s;color:#e9f8ff;selection-background-color:%s;border:1px solid %s;font-family:'%s';font-size:14px;}" % (
                T.rgba(T.bg1(), 230), T.rgba(m, 150), T.BODY, T.hexa(T.bg1()), T.rgba(m, 110), T.rgba(m, 140), T.BODY))


class TopBar(QtWidgets.QWidget):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setFixedHeight(54)
        self.chips = {}
        self.t0 = _now()
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(18, 8, 10, 8); lay.setSpacing(8)
        lay.addStretch(1)
        # v5: voice output device, visible in the top bar ("use my speakers" / "switch to headphones" by voice)
        self.audio_label = QtWidgets.QLabel("VOICE OUT")
        self.audio = AudioCombo(win)
        self.audio.setToolTip("Which speaker JARVIS talks on (or say \"Jarvis, use my speakers\")")
        lay.addWidget(self.audio_label); lay.addWidget(self.audio); lay.addSpacing(6)
        self.b_theme = HudButton(glyph="theme", w=34, h=28, tip="Colour theme (or say \"Jarvis, red theme\")")
        self.b_sound = HudButton(glyph="sound_on", w=34, h=28, tip="Sound effects on/off")
        self.b_min = HudButton(glyph="min", w=34, h=28, tip="Minimise")
        self.b_max = HudButton(glyph="max", w=34, h=28, tip="Maximise")
        self.b_close = HudButton(glyph="close", w=34, h=28, tip="Close the workspace (JARVIS keeps running in the tray)")
        for b in (self.b_theme, self.b_sound):
            lay.addWidget(b)
        lay.addSpacing(10)
        for b in (self.b_min, self.b_max, self.b_close):
            lay.addWidget(b)
        t = QtCore.QTimer(self); t.timeout.connect(self.update); t.start(500)

    def set_chip(self, key, text, on=True):
        if text is None:
            self.chips.pop(key, None)
        else:
            self.chips[key] = (text, on)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.win.frameless:
            h = self.window().windowHandle()
            if h is not None and h.startSystemMove():
                return
            self._drag = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        d = getattr(self, "_drag", None)
        if d is not None and e.buttons() & QtCore.Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - d)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.win.toggle_max()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect())
        m = T.main()
        # bottom rule with a bright centre segment
        g = QtGui.QLinearGradient(r.bottomLeft(), r.bottomRight())
        g.setColorAt(0, _c(m, 0.0)); g.setColorAt(0.5, _c(m, 0.75)); g.setColorAt(1, _c(m, 0.0))
        p.fillRect(QtCore.QRectF(r.left(), r.bottom() - 1.5, r.width(), 1.5), g)
        # logo: a mini reactor + wordmark
        paint_reactor(p, QtCore.QRectF(14, 7, 40, 40), _now() - self.t0, self.win.state if self.win.state != "boot" else "idle", 0.0, 1.0, label=False)
        f = T.font("display", 20, bold=True, spacing=5)
        p.setFont(f)
        p.setPen(_c(m, 0.5))
        for d in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(QtCore.QRectF(64 + d[0], 6 + d[1], 400, 26), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "J.A.R.V.I.S.")
        p.setPen(QtGui.QColor(255, 255, 255))
        p.drawText(QtCore.QRectF(64, 6, 400, 26), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "J.A.R.V.I.S.")
        p.setFont(T.font("display", 9, bold=True, spacing=3)); p.setPen(_c(T.light(), 0.7))
        p.drawText(QtCore.QRectF(66, 31, 400, 14), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "WORKSPACE  //  MARK V")
        # status chips in the middle
        p.setFont(T.font("display", 9, bold=True, spacing=1.6))
        fm = QtGui.QFontMetricsF(p.font())
        blink = (int((_now() - self.t0) * 2) % 2) == 0
        # chips stop short of the right-hand widgets (VOICE OUT + its combo + buttons), whatever the window width;
        # on a narrow window they slide left (not past the wordmark) before one is dropped
        right = self.audio_label.geometry().left() if self.audio_label.isVisible() else r.width() - 230
        total = sum(fm.horizontalAdvance(text) + 38 for text, _on in self.chips.values())
        x = max(330, min(r.width() / 2 - 230, right - 12 - total))
        for key, (text, on) in self.chips.items():
            w = fm.horizontalAdvance(text) + 30
            if x + w > right - 12:
                break
            box = QtCore.QRectF(x, 15, w, 24)
            p.setPen(QtGui.QPen(_c(m, 0.45), 1)); p.setBrush(_c(m, 0.07)); p.drawRect(box)
            col = (T.accent() if key == "focus" else QtGui.QColor(90, 255, 160)) if on else QtGui.QColor(255, 90, 80)
            if key == "focus" and not blink:
                col = _c(col, 0.4)
            p.setPen(QtCore.Qt.NoPen); p.setBrush(col); p.drawEllipse(QtCore.QPointF(box.left() + 12, box.center().y()), 3.5, 3.5)
            p.setPen(T.light()); p.drawText(box.adjusted(22, 0, -6, 0), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text)
            x += w + 8
        p.end()


class BootOverlay(QtWidgets.QWidget):
    """The power-on sequence painted over the whole workspace, then faded away."""
    finished = QtCore.Signal()
    LINES = ["J.A.R.V.I.S.  MARK V", "> neural interface|ONLINE", "> voice recognition|ONLINE",
             "> Tab uplink|SECURE", "> system telemetry|ONLINE", "> workspace|READY"]
    def __init__(self, parent, duration=2.8, hostname=""):
        super().__init__(parent)
        self.dur = duration; self.t0 = _now(); self.host = hostname
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(16)
        self.fade_len = 0.55
        self.resize(parent.size()); self.raise_(); self.show()

    def progress(self):
        return (_now() - self.t0) / self.dur

    def _tick(self):
        if _now() - self.t0 > self.dur + self.fade_len:
            self.timer.stop(); self.hide(); self.finished.emit(); self.deleteLater(); return
        self.update()

    def mousePressEvent(self, e):
        self.t0 = min(self.t0, _now() - self.dur)  # click to skip

    def paintEvent(self, ev):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        el = _now() - self.t0
        fade = 1.0 if el < self.dur else max(0.0, 1 - (el - self.dur) / self.fade_len)
        p.setOpacity(fade)
        r = QtCore.QRectF(self.rect())
        p.fillRect(r, T.bg0())
        # scanline sweep
        sy = (el / self.dur) * r.height()
        g = QtGui.QLinearGradient(0, sy - 80, 0, sy)
        g.setColorAt(0, _c(T.main(), 0.0)); g.setColorAt(1, _c(T.main(), 0.12))
        p.fillRect(QtCore.QRectF(0, sy - 80, r.width(), 80), g)
        p.fillRect(QtCore.QRectF(0, sy, r.width(), 1.5), _c(T.main(), 0.6))
        side = min(r.width(), r.height()) * 0.48
        b = min(1.0, el / (self.dur * 0.7))
        paint_reactor(p, QtCore.QRectF(r.center().x() - side / 2, r.center().y() - side / 2 - 40, side, side), el, "boot", 0.0, b, label=False)
        # typed lines, left-aligned as one centred block
        title_f = T.font("display", 26, bold=True, spacing=7); line_f = T.font("display", 14, bold=False, spacing=1.8)
        fm_t = QtGui.QFontMetricsF(title_f); fm_l = QtGui.QFontMetricsF(line_f)
        block_w = 420.0
        bx = r.center().x() - block_w / 2
        y = r.center().y() + side / 2 - 20
        per = self.dur * 0.75 / len(self.LINES)
        for i, line in enumerate(self.LINES):
            k = (el - i * per * 0.85) / per
            if k <= 0:
                break
            if i == 0:
                shown = line[:int(len(line) * min(1.0, k * 1.6))]
                p.setFont(title_f); p.setPen(QtGui.QColor(255, 255, 255))
                w = fm_t.horizontalAdvance(line)
                p.drawText(QtCore.QRectF(r.center().x() - w / 2, y, w + 40, fm_t.height()), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, shown)
                y += fm_t.height() + 14
                continue
            label, status = line.split("|")
            p.setFont(line_f)
            shown = label[:int(len(label) * min(1.0, k * 2.2))]
            p.setPen(_c(T.light(), 0.85))
            p.drawText(QtCore.QRectF(bx, y, block_w, fm_l.height()), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, shown)
            if k > 0.6:
                lw = fm_l.horizontalAdvance(label + "  "); sw = fm_l.horizontalAdvance(status)
                frac = min(1.0, (k - 0.6) / 0.4)
                p.setPen(QtGui.QPen(_c(T.main(), 0.45), 1.2, QtCore.Qt.DotLine))
                x0 = bx + lw; x1 = bx + block_w - sw - 10
                p.drawLine(QtCore.QPointF(x0, y + fm_l.height() * 0.62), QtCore.QPointF(x0 + (x1 - x0) * frac, y + fm_l.height() * 0.62))
                if frac >= 1.0:
                    p.setPen(T.main())
                    p.drawText(QtCore.QRectF(bx, y, block_w, fm_l.height()), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, status)
            y += fm_l.height() + 6
        p.setFont(T.font("display", 9, spacing=2)); p.setPen(_c(T.light(), 0.5))
        p.drawText(QtCore.QRectF(0, r.bottom() - 30, r.width(), 20), QtCore.Qt.AlignCenter,
                   ("STARK INDUSTRIES  ·  " + (self.host + "  ·  " if self.host else "") + "CLICK TO SKIP"))
        p.end()


class Body(QtWidgets.QWidget):
    """The painted background: deep navy, faint hex grid, centre glow, vignette."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._bg = None; self._bg_key = None

    def _make_bg(self):
        w, h = max(1, self.width()), max(1, self.height())
        key = (w, h, T.current())
        if key == self._bg_key:
            return self._bg
        img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
        p = QtGui.QPainter(img); p.setRenderHint(QtGui.QPainter.Antialiasing)
        g = QtGui.QLinearGradient(0, 0, 0, h); g.setColorAt(0, T.bg1()); g.setColorAt(1, T.bg0())
        p.fillRect(img.rect(), g)
        # hex grid
        s = 26.0; hh = s * math.sqrt(3)
        p.setPen(QtGui.QPen(_c(T.main(), 0.045), 1)); p.setBrush(QtCore.Qt.NoBrush)
        y = 0.0; row = 0
        while y < h + hh:
            x = (s * 1.5) if row % 2 else 0.0
            while x < w + s * 3:
                poly = QtGui.QPolygonF([QtCore.QPointF(x + s * math.cos(math.radians(a)), y + s * math.sin(math.radians(a))) for a in range(0, 360, 60)])
                p.drawPolygon(poly)
                x += s * 3
            y += hh / 2; row += 1
        # centre glow
        rg = QtGui.QRadialGradient(w / 2, h * 0.42, max(w, h) * 0.5)
        rg.setColorAt(0, _c(T.main(), 0.10)); rg.setColorAt(1, _c(T.main(), 0.0))
        p.fillRect(img.rect(), rg)
        # vignette
        vg = QtGui.QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        vg.setColorAt(0.6, QtGui.QColor(0, 0, 0, 0)); vg.setColorAt(1.0, QtGui.QColor(0, 0, 0, 150))
        p.fillRect(img.rect(), vg)
        p.end()
        self._bg = img; self._bg_key = key
        return img

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.drawImage(0, 0, self._make_bg())
        # window border
        p.setPen(QtGui.QPen(_c(T.main(), 0.35), 1)); p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()


class Footer(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22); self.text = ""; self.t0 = _now()
        t = QtCore.QTimer(self); t.timeout.connect(self.update); t.start(1000)

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setFont(T.font("display", 8.5, spacing=1.8)); p.setPen(_c(T.light(), 0.5))
        p.drawText(QtCore.QRectF(self.rect()).adjusted(18, 0, -18, 0), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self.text)
        p.drawText(QtCore.QRectF(self.rect()).adjusted(18, 0, -18, 0), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                   "CTRL+SHIFT+J TOGGLES THIS WINDOW  ·  \"JARVIS, STATUS REPORT\"")
        p.end()


# ======================================================================
#  the window
# ======================================================================
class Workspace(QtWidgets.QWidget):
    """Callbacks (all optional, set by app.py):
       on_command(text)   typed command -> same path as speech
       on_talk()          start listening (as if he said "Jarvis")
       on_launch(entry)   quick-launch tile
       on_media(action)   play_pause / next / previous
       on_open(path)      open a clip
       on_theme(name)     theme picked
       on_sounds(bool)    sound toggle
       on_launch_changed(list)  save edited tiles"""
    visibility = QtCore.Signal(bool)
    stats_ready = QtCore.Signal(object)
    weather_ready = QtCore.Signal(object)
    media_ready = QtCore.Signal(object)
    clips_ready = QtCore.Signal(object)
    thumb_ready = QtCore.Signal(str, str)

    def __init__(self, cfg=None, data_dir=None, hostname="", icon_for=None, demo=False):
        super().__init__(None)
        T.load_fonts()
        self.cfg = cfg or {}
        self.wcfg = self.cfg.get("workspace", {}) if isinstance(self.cfg.get("workspace", {}), dict) else {}
        self.data_dir = data_dir or os.path.join(os.path.expanduser("~"), ".jarvis-ws")
        self.state = "idle"
        self.frameless = not bool(self.wcfg.get("native_frame", False))
        self.booted_once = False
        self.demo = demo
        self.on_command = self.on_talk = self.on_launch = self.on_media = self.on_open = None
        self.on_theme = self.on_sounds = self.on_launch_changed = self.on_open_folder = None
        self.sounds_on = True
        self._pollers = []
        self.setWindowTitle("J.A.R.V.I.S. — Workspace")
        flags = QtCore.Qt.Window | (QtCore.Qt.FramelessWindowHint if self.frameless else QtCore.Qt.Widget)
        self.setWindowFlags(flags)
        self.setMinimumSize(1080, 680)
        self.setMouseTracking(True)

        outer = QtWidgets.QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        self.body = Body(self); outer.addWidget(self.body)
        self.body.setMouseTracking(True)
        root = QtWidgets.QVBoxLayout(self.body); m = 6 if self.frameless else 0
        root.setContentsMargins(m, m, m, m); root.setSpacing(0)
        self.top = TopBar(self); root.addWidget(self.top)
        if not self.frameless:
            for b in (self.top.b_min, self.top.b_max, self.top.b_close):
                b.hide()
        grid_host = QtWidgets.QWidget(); root.addWidget(grid_host, 1)
        g = QtWidgets.QHBoxLayout(grid_host); g.setContentsMargins(12, 12, 12, 6); g.setSpacing(12)

        left = QtWidgets.QVBoxLayout(); left.setSpacing(12)
        self.p_clock = ClockPanel(); self.p_weather = WeatherPanel(); self.p_sys = SystemPanel()
        left.addWidget(self.p_clock, 3); left.addWidget(self.p_weather, 3); left.addWidget(self.p_sys, 5)

        center = QtWidgets.QVBoxLayout(); center.setSpacing(10)
        self.reactor = ReactorWidget(); self.p_convo = ConvoPanel(); self.cmd = CommandBar()
        center.addWidget(self.reactor, 6); center.addWidget(self.p_convo, 4); center.addWidget(self.cmd, 0)

        right = QtWidgets.QVBoxLayout(); right.setSpacing(12)
        self.p_media = MediaPanel()
        self.p_launch = LaunchPanel(self.wcfg.get("quick_launch") or DEFAULT_LAUNCH, icon_for or (lambda e: None))
        self.p_clips = ClipsPanel()
        self.p_tab = TabScreenPanel()          # v5: what Tab is doing right now
        right.addWidget(self.p_tab, 4); right.addWidget(self.p_media, 3); right.addWidget(self.p_launch, 5); right.addWidget(self.p_clips, 4)

        g.addLayout(left, 10); g.addLayout(center, 14); g.addLayout(right, 10)
        self.footer = Footer(); root.addWidget(self.footer)
        self.panels = [self.p_clock, self.p_weather, self.p_sys, self.p_convo, self.p_tab, self.p_media, self.p_launch, self.p_clips]

        # wiring
        self.cmd.submitted.connect(self._typed)
        self.cmd.talk.connect(lambda: self.on_talk and self.on_talk())
        self.reactor.clicked.connect(lambda: self.on_talk and self.on_talk())
        self.p_media.command.connect(lambda a: self.on_media and self.on_media(a))
        self.p_launch.launch.connect(lambda e: self.on_launch and self.on_launch(e))
        self.p_launch.changed.connect(lambda l: self.on_launch_changed and self.on_launch_changed(l))
        self.p_clips.open_path.connect(lambda path: self.on_open and self.on_open(path))
        self.p_clips.refresh.connect(self.refresh_clips)
        self.p_clips.open_folder.connect(lambda: self.on_open_folder and self.on_open_folder())
        self.top.b_min.clicked.connect(self.showMinimized)
        self.top.b_max.clicked.connect(self.toggle_max)
        self.top.b_close.clicked.connect(self.close)
        self.top.b_theme.clicked.connect(self._theme_menu)
        self.top.b_sound.clicked.connect(lambda: self.set_sounds(not self.sounds_on, user=True))
        self.stats_ready.connect(self.p_sys.set_stats)
        self.weather_ready.connect(self.p_weather.set_data)
        self.media_ready.connect(self.p_media.set_data)
        self.clips_ready.connect(self._got_clips)
        self.thumb_ready.connect(self.p_clips.set_thumb)
        T.on_change(lambda n: self.restyle())
        self.top.set_chip("voice", "VOICE ONLINE"); self.top.set_chip("tab", "TAB LINK READY")
        self.footer.text = ("J.A.R.V.I.S. MK V  ·  " + (hostname.upper() + "  ·  " if hostname else "") + "ALL SYSTEMS NOMINAL")
        self.restyle()
        self._save_timer = QtCore.QTimer(self); self._save_timer.setSingleShot(True); self._save_timer.timeout.connect(self.save_geometry)
        self.restore_geometry()

    # ---------- styling ----------
    def restyle(self):
        self.p_convo.restyle(); self.cmd.restyle(); self.p_clips.restyle()
        self.top.audio.restyle()
        self.top.audio_label.setStyleSheet("color:%s;font-family:'%s';font-size:10px;font-weight:700;letter-spacing:2px;" % (T.hexa(T.light()), T.DISPLAY))
        self.body._bg_key = None
        QtWidgets.QToolTip.setFont(T.font("body", 14))
        self.setStyleSheet("QToolTip{color:#e9f8ff;background:%s;border:1px solid %s;padding:4px;}"
                           "QMenu{background:%s;color:#e9f8ff;border:1px solid %s;font-family:'%s';font-size:15px;}"
                           "QMenu::item:selected{background:%s;}" % (
                               T.hexa(T.bg1()), T.rgba(T.main(), 160), T.hexa(T.bg1()), T.rgba(T.main(), 140), T.BODY, T.rgba(T.main(), 90)))
        for w in self.findChildren(QtWidgets.QWidget):
            w.update()
        self.update()

    def _theme_menu(self):
        m = QtWidgets.QMenu(self)
        for name in T.THEMES:
            a = m.addAction(T.label(name)); a.setCheckable(True); a.setChecked(name == T.current())
            a.triggered.connect(lambda _=False, n=name: (T.set_theme(n), self.on_theme and self.on_theme(n)))
        m.exec(self.top.b_theme.mapToGlobal(QtCore.QPoint(0, self.top.b_theme.height() + 4)))

    def set_sounds(self, on, user=False):
        self.sounds_on = bool(on)
        self.top.b_sound.glyph = "sound_on" if on else "sound_off"; self.top.b_sound.update()
        if user and self.on_sounds:
            self.on_sounds(self.sounds_on)

    # ---------- live inputs from JARVIS ----------
    @QtCore.Slot(str)
    def set_state(self, s):
        self.state = s; self.reactor.set_state(s)
        self.top.set_chip("voice", "MIC PAUSED" if s == "paused" else "VOICE ONLINE", s != "paused")

    @QtCore.Slot(float)
    def set_level(self, v):
        self.reactor.set_level(v)

    @QtCore.Slot(str)
    def set_caption(self, text):
        self.reactor.set_caption(text)

    def add_log(self, who, text, ts=None):
        self.p_convo.add(who, text, ts)

    # ---------- v5 ----------
    HEARD_WORDS = {"heard": "HEARD", "ignored": "IGNORED", "ask": "DID YOU MEAN", "cancel": "CANCELLED", "asleep": "ASLEEP", "typed": "TYPED"}
    @QtCore.Slot(str, str)
    def set_heard(self, text, status="heard"):
        tag = self.HEARD_WORDS.get(status, "HEARD")
        self.reactor.set_caption(f"{tag} ▸ “{text}”" if status != "ask" else f"DID YOU MEAN ▸ {text}")
        if status == "asleep":
            self.top.set_chip("voice", "ASLEEP · SAY \"WAKE UP\"", False)

    def show_log(self):
        self.show(); self.raise_(); self.activateWindow()
        self.p_convo.flash() if hasattr(self.p_convo, "flash") else None
        sb = self.p_convo.view.verticalScrollBar(); sb.setValue(sb.maximum())

    def show_help(self, lines):
        self.add_log("jarvis", "Things you can say:\n• " + "\n• ".join(lines))
        self.show_log()

    def set_looking(self):
        self.reactor.set_caption("Looking… (Tab is reading your screen)")
        self.top.set_chip("tab", "TAB · LOOKING…")
        QtCore.QTimer.singleShot(45000, lambda: self.top.set_chip("tab", "TAB LINK READY"))

    def add_activity(self, act):
        self.p_tab.add(act)
        self.top.set_chip("tab", "TAB · WORKING" if (act.get("progress") or 0) < 100 else "TAB LINK READY")

    def set_audio_current(self, name):
        self.top.audio.set_current(name)

    def set_focus(self, end_ts, total):
        self.p_clock.focus_end = end_ts; self.p_clock.focus_total = total
        if end_ts:
            left = max(0, int(end_ts - time.time()))
            self.top.set_chip("focus", "FOCUS %02d:%02d" % (left // 60, left % 60))
        else:
            self.top.set_chip("focus", None)
        self.p_clock.update()

    def _typed(self, text):
        if self.on_command:
            self.on_command(text)

    # ---------- window behaviour ----------
    def toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    EDGE = 7
    def _edges(self, pos):
        e = QtCore.Qt.Edge(0); r = self.rect()
        if pos.x() <= self.EDGE: e |= QtCore.Qt.LeftEdge
        if pos.x() >= r.width() - self.EDGE: e |= QtCore.Qt.RightEdge
        if pos.y() <= self.EDGE: e |= QtCore.Qt.TopEdge
        if pos.y() >= r.height() - self.EDGE: e |= QtCore.Qt.BottomEdge
        return e

    def eventFilter(self, obj, ev):
        return False

    def mouseMoveEvent(self, e):
        if self.frameless and not self.isMaximized():
            ed = self._edges(e.position().toPoint())
            cur = QtCore.Qt.ArrowCursor
            if ed in (QtCore.Qt.LeftEdge | QtCore.Qt.TopEdge, QtCore.Qt.RightEdge | QtCore.Qt.BottomEdge): cur = QtCore.Qt.SizeFDiagCursor
            elif ed in (QtCore.Qt.RightEdge | QtCore.Qt.TopEdge, QtCore.Qt.LeftEdge | QtCore.Qt.BottomEdge): cur = QtCore.Qt.SizeBDiagCursor
            elif ed & (QtCore.Qt.LeftEdge | QtCore.Qt.RightEdge): cur = QtCore.Qt.SizeHorCursor
            elif ed & (QtCore.Qt.TopEdge | QtCore.Qt.BottomEdge): cur = QtCore.Qt.SizeVerCursor
            self.setCursor(cur)
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if self.frameless and e.button() == QtCore.Qt.LeftButton and not self.isMaximized():
            ed = self._edges(e.position().toPoint())
            if ed and self.windowHandle() is not None:
                self.windowHandle().startSystemResize(ed); return
        super().mousePressEvent(e)

    def _state_path(self):
        return os.path.join(self.data_dir, "workspace_window.json")

    def save_geometry(self):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            st = {"geometry": base64.b64encode(bytes(self.saveGeometry())).decode("ascii"),
                  "maximized": self.isMaximized()}
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(st, f)
        except Exception as e:
            log.info("couldn't save the workspace window position: %r", e)

    def restore_geometry(self):
        ok = False
        try:
            st = json.load(open(self._state_path(), encoding="utf-8"))
            ok = self.restoreGeometry(QtCore.QByteArray(base64.b64decode(st["geometry"])))
            self._want_max = bool(st.get("maximized"))
        except Exception:
            self._want_max = False
        # never come back off-screen (a monitor was unplugged)
        if ok:
            fg = self.frameGeometry()
            if not any(s.availableGeometry().intersects(fg) for s in QtGui.QGuiApplication.screens()):
                ok = False
        if not ok:
            scr = QtGui.QGuiApplication.primaryScreen()
            av = scr.availableGeometry() if scr else QtCore.QRect(0, 0, 1600, 900)
            w = min(1480, int(av.width() * 0.88)); h = min(900, int(av.height() * 0.88))
            w = max(w, min(self.minimumWidth(), av.width())); h = max(h, min(self.minimumHeight(), av.height()))
            self.setGeometry(av.left() + (av.width() - w) // 2, av.top() + (av.height() - h) // 2, w, h)

    def moveEvent(self, e):
        super().moveEvent(e); self._save_timer.start(800)

    def resizeEvent(self, e):
        super().resizeEvent(e); self._save_timer.start(800)
        for c in self.findChildren(BootOverlay):
            c.resize(self.size())

    def closeEvent(self, e):
        # closing the window only hides the workspace; JARVIS keeps running in the tray
        self.save_geometry()
        e.ignore(); self.hide()

    def open(self, boot=None):
        mode = self.wcfg.get("boot_animation", "first")
        do_boot = boot if boot is not None else (mode == "always" or (mode == "first" and not self.booted_once))
        if self._want_max:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_(); self.activateWindow()
        if do_boot:
            self.play_boot()
        self.cmd.edit.setFocus()

    def play_boot(self, duration=2.8):
        self.booted_once = True
        for pnl in self.panels:
            pnl.reveal = 0.0
        ov = BootOverlay(self, duration, getattr(self, "hostname", ""))
        ov.finished.connect(self._reveal_panels)

    def _reveal_panels(self):
        for i, pnl in enumerate(self.panels):
            a = QtCore.QVariantAnimation(self); a.setStartValue(0.0); a.setEndValue(1.0); a.setDuration(520)
            a.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            a.valueChanged.connect(lambda v, pnl=pnl: (setattr(pnl, "reveal", float(v)), pnl.update()))
            QtCore.QTimer.singleShot(70 * i, a.start)

    def showEvent(self, e):
        super().showEvent(e)
        self.reactor.fps(30)
        self.visibility.emit(True)
        for pl in self._pollers:
            pl.kick.set()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.reactor.fps(2)
        self.visibility.emit(False)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QtCore.QEvent.WindowStateChange:
            self.reactor.fps(2 if self.isMinimized() else 30)

    def toggle(self):
        if self.isVisible() and not self.isMinimized():
            self.close()
        else:
            self.open()

    # ---------- background data ----------
    def is_live(self):
        return self.isVisible() and not self.isMinimized()

    def start_data(self, weather_fn=None, clip_folders_fn=None, thumbs_dir=None):
        self.thumbs_dir = thumbs_dir or os.path.join(self.data_dir, "thumbs")
        self.clip_folders_fn = clip_folders_fn or (lambda: wsdata.clip_folders())
        try:
            stats = wsdata.Stats()
            self._pollers.append(wsdata.Poller("ws-stats", stats.sample, self.stats_ready.emit, 1.5, self.is_live))
        except Exception as e:
            log.info("system stats unavailable: %r", e)
        if weather_fn:
            self._pollers.append(wsdata.Poller("ws-weather", weather_fn, self.weather_ready.emit, 900, self.is_live))
        self._pollers.append(wsdata.Poller("ws-media", wsdata.now_playing, self.media_ready.emit, 2.5, self.is_live))
        self._pollers.append(wsdata.Poller("ws-clips", self._scan_clips, self.clips_ready.emit, 60, self.is_live))
        for pl in self._pollers:
            pl.start()

    def refresh_clips(self):
        for pl in self._pollers:
            if pl.name == "ws-clips":
                pl.kick.set()

    def _scan_clips(self):
        return wsdata.recent_clips(self.clip_folders_fn(), limit=6)

    def _got_clips(self, clips):
        old = [c[0] for c in getattr(self, "_clips", [])]
        self._clips = clips or []
        if [c[0] for c in self._clips] != old or not self.p_clips.tiles:
            self.p_clips.set_clips(self._clips)
            def thumbs(items=list(self._clips)):
                for path, mtime, size in items:
                    t = wsdata.make_thumb(path, mtime, self.thumbs_dir)
                    if t:
                        self.thumb_ready.emit(path, t)
                wsdata.prune_thumbs(self.thumbs_dir)
            threading.Thread(target=thumbs, name="ws-thumbs", daemon=True).start()
