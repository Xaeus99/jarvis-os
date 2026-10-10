"""v4 art: the arc reactor (workspace centrepiece) and the HUD panel frame. Pure QPainter, no images."""
from __future__ import annotations
import math, random
from PySide6 import QtCore, QtGui
from . import theme as T

def _c(col, a):
    c = QtGui.QColor(col); c.setAlphaF(max(0.0, min(1.0, a))); return c

def _arc(p, cx, cy, r, start_deg, span_deg, pen):
    p.setPen(pen); p.setBrush(QtCore.Qt.NoBrush)
    p.drawArc(QtCore.QRectF(cx - r, cy - r, 2 * r, 2 * r), int(-start_deg * 16), int(-span_deg * 16))

def _glow_arc(p, cx, cy, r, start, span, col, width, alpha, glow=True):
    """A stroke with a soft halo (three wider, fainter passes underneath)."""
    gk = T.glow()
    if glow and gk > 0.05:
        for w_mul, a_mul in ((5.0, 0.07), (3.0, 0.12), (1.8, 0.25)):
            pen = QtGui.QPen(_c(col, alpha * a_mul * gk), width * w_mul); pen.setCapStyle(QtCore.Qt.FlatCap)
            _arc(p, cx, cy, r, start, span, pen)
    pen = QtGui.QPen(_c(col, alpha), width); pen.setCapStyle(QtCore.Qt.FlatCap)
    _arc(p, cx, cy, r, start, span, pen)

def _noise(a, t):
    return 0.5 + 0.25 * math.sin(a * 7 + t * 5.1) + 0.15 * math.sin(a * 13 - t * 8.3) + 0.1 * math.sin(a * 23 + t * 11.7)

STATE_WORDS = {"idle": "STANDING BY", "listening": "LISTENING", "thinking": "PROCESSING",
               "speaking": "RESPONDING", "paused": "MIC PAUSED", "boot": "INITIALIZING"}

def paint_reactor(p: QtGui.QPainter, rect: QtCore.QRectF, t: float, state: str = "idle", level: float = 0.0,
                  boot: float = 1.0, label: bool = True):
    """The arc reactor. boot 0->1 draws it in; level 0..1 is mic/speech loudness."""
    p.save()
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    cx, cy = rect.center().x(), rect.center().y()
    R = min(rect.width(), rect.height()) / 2.0 * 0.94
    if R < 20:
        p.restore(); return
    paused = state == "paused"
    main = QtGui.QColor(150, 165, 175) if paused else T.main()
    light = QtGui.QColor(200, 210, 215) if paused else T.light()
    deep = T.deep()
    acc = QtGui.QColor(150, 150, 150) if paused else T.accent()
    dim = 0.45 if paused else 1.0
    b = max(0.0, min(1.0, boot))
    ease = 1 - (1 - b) ** 3
    speed = {"idle": 1.0, "listening": 2.2, "thinking": 5.0, "speaking": 1.6, "paused": 0.15, "boot": 3.0}.get(state, 1.0)
    rot = t * 9.0 * speed
    breathe = 0.5 + 0.5 * math.sin(t * 1.6)
    energy = {"idle": 0.55 + 0.15 * breathe, "listening": 0.8 + 0.6 * level, "thinking": 0.95,
              "speaking": 0.85 + 0.35 * level + 0.1 * math.sin(t * 9), "paused": 0.3, "boot": 0.9}.get(state, 0.6)

    # 0. ambient halo behind everything
    g = QtGui.QRadialGradient(cx, cy, R * 1.15)
    gk = T.glow()
    g.setColorAt(0.0, _c(main, 0.30 * energy * ease * gk)); g.setColorAt(0.35, _c(main, 0.10 * energy * ease * gk))
    g.setColorAt(1.0, _c(main, 0.0))
    p.setPen(QtCore.Qt.NoPen); p.setBrush(g); p.drawEllipse(QtCore.QPointF(cx, cy), R * 1.15, R * 1.15)

    # 1. outer tick ring (draws in clockwise during boot)
    p.save(); p.translate(cx, cy); p.rotate(rot * 0.12)
    n = 180; shown = int(n * ease)
    for i in range(shown):
        a = 2 * math.pi * i / n
        long = i % 15 == 0; mid = i % 5 == 0
        r1 = R * (0.925 if long else 0.945 if mid else 0.955); r2 = R * 0.975
        al = (0.85 if long else 0.5 if mid else 0.28) * dim
        if state == "listening":
            al *= 0.6 + 0.8 * max(0.0, math.sin(a * 4 - t * 6)) * (0.4 + level)
        p.setPen(QtGui.QPen(_c(main, al), 1.6 if long else 1.0))
        p.drawLine(QtCore.QPointF(r1 * math.cos(a), r1 * math.sin(a)), QtCore.QPointF(r2 * math.cos(a), r2 * math.sin(a)))
    p.restore()

    # 2. three broken data rings at different speeds
    for (rr, wid, segs, gap, spd, col_a) in ((0.885, 2.0, 3, 18, 0.5, 0.75), (0.84, 1.2, 7, 9, -0.9, 0.45), (0.80, 5.0, 4, 50, 0.3, 0.32)):
        span = (360 / segs - gap) * ease
        for k in range(segs):
            _glow_arc(p, cx, cy, R * rr, rot * spd + k * 360 / segs, span, main, max(1.0, wid * R / 260), col_a * dim * (0.8 + 0.4 * energy), glow=wid > 1.5)
    # accent arcs (the warm second colour)
    for k, (st, sp) in enumerate(((200, 34), (20, 14))):
        _glow_arc(p, cx, cy, R * 0.80, st - rot * 0.2 + 6 * math.sin(t * 0.7 + k), sp * ease, acc, max(2.0, 5 * R / 260), 0.9 * dim)

    # 3. listening: a live waveform ring around the reactor
    if state in ("listening", "speaking") and b >= 1:
        bars = 96
        amp = (0.12 + 0.88 * min(1.0, level * 1.3)) if state == "listening" else (0.25 + 0.6 * min(1.0, level + 0.3 * abs(math.sin(t * 7))))
        p.setPen(QtCore.Qt.NoPen)
        for i in range(bars):
            a = 2 * math.pi * i / bars
            ln = R * 0.10 * amp * _noise(a, t)
            r1 = R * 0.70
            pen = QtGui.QPen(_c(light, 0.85), max(1.5, R * 0.012)); pen.setCapStyle(QtCore.Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QtCore.QPointF(cx + r1 * math.cos(a), cy + r1 * math.sin(a)),
                       QtCore.QPointF(cx + (r1 + ln) * math.cos(a), cy + (r1 + ln) * math.sin(a)))

    # 4. the reactor housing: dark disc and a bright rim
    hous = R * 0.66
    hg = QtGui.QRadialGradient(cx, cy, hous)
    hg.setColorAt(0.0, _c(T.bg1(), 0.95)); hg.setColorAt(0.85, _c(T.bg0(), 0.98)); hg.setColorAt(1.0, _c(deep, 0.9))
    p.setPen(QtCore.Qt.NoPen); p.setBrush(hg); p.drawEllipse(QtCore.QPointF(cx, cy), hous * ease, hous * ease)
    _glow_arc(p, cx, cy, hous, -90, 360 * ease, main, max(1.5, R * 0.008), 0.9 * dim)

    # 5. ten coils (the Mark I reactor ring), lit in sequence while thinking
    coils = 10; c_r = R * 0.50; c_w = R * 0.13
    for i in range(coils):
        if i >= int(coils * ease + 0.001):
            break
        a0 = -90 + i * 36 + rot * 0.15
        lit = 0.55 + 0.45 * energy
        if state == "thinking":
            ph = (t * 1.6) % 1.0
            d = abs(((i / coils) - ph + 0.5) % 1.0 - 0.5)
            lit = 0.35 + 0.65 * max(0.0, 1 - d * 5)
        elif state == "speaking":
            lit = 0.6 + 0.4 * abs(math.sin(t * 6 + i * 0.6)) * (0.5 + level)
        pen = QtGui.QPen(_c(deep, 0.95 * dim), c_w); pen.setCapStyle(QtCore.Qt.FlatCap)
        _arc(p, cx, cy, c_r, a0 + 3, 30, pen)
        pen = QtGui.QPen(_c(light, min(1.0, 0.55 * lit) * dim), c_w * 0.62); pen.setCapStyle(QtCore.Qt.FlatCap)
        _arc(p, cx, cy, c_r, a0 + 5, 26, pen)
        pen = QtGui.QPen(_c(QtGui.QColor(255, 255, 255), min(1.0, 0.35 * lit) * dim), c_w * 0.16); pen.setCapStyle(QtCore.Qt.FlatCap)
        _arc(p, cx, cy, c_r, a0 + 7, 22, pen)
    p.setBrush(QtCore.Qt.NoBrush)
    p.setPen(QtGui.QPen(_c(main, 0.6 * dim), 1.2))
    p.drawEllipse(QtCore.QPointF(cx, cy), c_r + c_w / 2 + 3, c_r + c_w / 2 + 3)
    p.drawEllipse(QtCore.QPointF(cx, cy), c_r - c_w / 2 - 3, c_r - c_w / 2 - 3)

    # 6. thinking: a fast comet sweeping the inner track
    if state == "thinking":
        for k in range(12):
            _arc(p, cx, cy, R * 0.355, (t * 420) % 360 - k * 5, 5,
                 QtGui.QPen(_c(light, 0.9 * (1 - k / 12)), max(2.0, R * 0.02), QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
    else:
        pen = QtGui.QPen(_c(main, 0.5 * dim), max(1.0, R * 0.006))
        for k in range(3):
            _arc(p, cx, cy, R * 0.355, -rot * 0.6 + k * 120, 70 * ease, pen)

    # 7. the core: a white-hot centre that breathes with speech and the mic
    core = R * (0.26 + 0.03 * breathe + (0.05 * level if state in ("listening", "speaking") else 0)) * ease
    cg = QtGui.QRadialGradient(cx, cy, core * 1.9)
    cg.setColorAt(0.0, _c(QtGui.QColor(255, 255, 255), 0.98 * dim))
    cg.setColorAt(0.18, _c(light, 0.95 * dim))
    if T.core2() is not None and not paused:
        cg.setColorAt(0.30, _c(T.core2(), 0.85 * dim))
    cg.setColorAt(0.45, _c(main, 0.60 * energy * dim * min(1.0, 0.5 + gk / 2)))
    cg.setColorAt(0.75, _c(main, 0.16 * energy * dim * gk))
    cg.setColorAt(1.0, _c(main, 0.0))
    p.setPen(QtCore.Qt.NoPen); p.setBrush(cg); p.drawEllipse(QtCore.QPointF(cx, cy), core * 1.9, core * 1.9)
    # inner triangle (Mark II style) faint
    p.setBrush(QtCore.Qt.NoBrush); p.setPen(QtGui.QPen(_c(QtGui.QColor(255, 255, 255), 0.35 * dim * ease), max(1.0, R * 0.008)))
    tri = QtGui.QPolygonF([QtCore.QPointF(cx + core * 0.95 * math.cos(math.radians(-90 + rot * 0.3 + k * 120)),
                                          cy + core * 0.95 * math.sin(math.radians(-90 + rot * 0.3 + k * 120))) for k in range(3)])
    p.drawPolygon(tri)

    # 8. speaking: ripples leaving the core
    if state == "speaking":
        for k in range(3):
            ph = ((t * 0.9) + k / 3.0) % 1.0
            rr = R * (0.30 + 0.62 * ph)
            p.setPen(QtGui.QPen(_c(light, 0.45 * (1 - ph)), max(1.0, R * 0.01)))
            p.drawEllipse(QtCore.QPointF(cx, cy), rr, rr)

    # 9. state word under the reactor
    if label:
        f = T.font("display", max(9, R * 0.055), bold=True, spacing=R * 0.012)
        p.setFont(f)
        word = STATE_WORDS.get(state, state.upper())
        tr = QtCore.QRectF(cx - R, cy + R * 0.70, 2 * R, R * 0.12)
        p.setPen(_c(acc if state == "listening" else light, 0.95 * max(0.4, dim) * ease))
        p.drawText(tr, QtCore.Qt.AlignCenter, word)
    p.restore()


def paint_frame(p: QtGui.QPainter, r: QtCore.QRectF, title: str = "", reveal: float = 1.0, hot: bool = False):
    """A HUD panel: dark glass, hairline border, bright corner brackets, a title tab."""
    p.save(); p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    main = T.main()
    cut = 14.0
    path = QtGui.QPainterPath()
    path.moveTo(r.left() + cut, r.top()); path.lineTo(r.right(), r.top())
    path.lineTo(r.right(), r.bottom() - cut); path.lineTo(r.right() - cut, r.bottom())
    path.lineTo(r.left(), r.bottom()); path.lineTo(r.left(), r.top() + cut); path.closeSubpath()
    g = QtGui.QLinearGradient(r.topLeft(), r.bottomLeft())
    b1, b0 = T.bg1(), T.bg0()
    g.setColorAt(0.0, QtGui.QColor(b1.red(), b1.green(), b1.blue(), int(235 * reveal)))
    g.setColorAt(1.0, QtGui.QColor(b0.red(), b0.green(), b0.blue(), int(235 * reveal)))
    p.setPen(QtCore.Qt.NoPen); p.setBrush(g); p.drawPath(path)
    # inner top sheen
    sh = QtGui.QLinearGradient(r.topLeft(), QtCore.QPointF(r.left(), r.top() + 60))
    sh.setColorAt(0.0, _c(main, 0.10 * reveal)); sh.setColorAt(1.0, _c(main, 0.0))
    p.setBrush(sh); p.drawPath(path)
    # border, traced in by `reveal`
    pen = QtGui.QPen(_c(main, (0.55 if hot else 0.30) * reveal), 1.0)
    p.setBrush(QtCore.Qt.NoBrush); p.setPen(pen)
    if reveal >= 0.999:
        p.drawPath(path)
    else:
        length = path.length()
        stroker = QtGui.QPainterPath(); steps = 80
        for i in range(int(steps * reveal) + 1):
            pt = path.pointAtPercent(min(1.0, i / steps))
            (stroker.moveTo if i == 0 else stroker.lineTo)(pt)
        p.drawPath(stroker)
    # corner brackets
    L = 16
    pen = QtGui.QPen(_c(main, 0.95 * reveal), 2.0); pen.setCapStyle(QtCore.Qt.SquareCap); p.setPen(pen)
    p.drawLine(QtCore.QPointF(r.right() - L, r.top()), QtCore.QPointF(r.right(), r.top()))
    p.drawLine(QtCore.QPointF(r.right(), r.top()), QtCore.QPointF(r.right(), r.top() + L))
    p.drawLine(QtCore.QPointF(r.left(), r.bottom() - L), QtCore.QPointF(r.left(), r.bottom()))
    p.drawLine(QtCore.QPointF(r.left(), r.bottom()), QtCore.QPointF(r.left() + L, r.bottom()))
    p.drawLine(QtCore.QPointF(r.left(), r.top() + cut), QtCore.QPointF(r.left() + cut, r.top()))
    p.drawLine(QtCore.QPointF(r.right() - cut, r.bottom()), QtCore.QPointF(r.right(), r.bottom() - cut))
    # title
    if title:
        f = T.font("display", 11, bold=True, spacing=2.4)
        p.setFont(f)
        fm = QtGui.QFontMetricsF(f)
        x = r.left() + cut + 10; y = r.top() + 8
        p.setPen(QtCore.Qt.NoPen); p.setBrush(_c(main, 0.9 * reveal))
        p.drawRect(QtCore.QRectF(x - 8, y + 4, 3, 11))
        p.setPen(_c(T.light(), 0.95 * reveal))
        p.drawText(QtCore.QRectF(x, y, r.width() - cut - 30, 20), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, title.upper())
        # a faint rule after the title
        tw = fm.horizontalAdvance(title.upper())
        p.setPen(QtGui.QPen(_c(main, 0.22 * reveal), 1.0))
        p.drawLine(QtCore.QPointF(x + tw + 10, y + 10), QtCore.QPointF(r.right() - 12, y + 10))
    p.restore()


def paint_ring_gauge(p, rect: QtCore.QRectF, value, label: str, sub: str = "", warn=80.0):
    """A circular gauge: 0..100, colour shifts to the accent above `warn`."""
    p.save(); p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    side = min(rect.width(), rect.height() - 16)
    cx = rect.center().x(); cy = rect.top() + side / 2 + 2
    r = side / 2 - 6
    main = T.main(); hot = T.accent()
    v = None if value is None else max(0.0, min(100.0, float(value)))
    pen = QtGui.QPen(_c(T.deep(), 0.55), max(4.0, r * 0.16)); pen.setCapStyle(QtCore.Qt.FlatCap)
    _arc(p, cx, cy, r, 135, 270, pen)
    # ticks
    for i in range(28):
        a = math.radians(135 + i * 270 / 27)
        p.setPen(QtGui.QPen(_c(main, 0.35), 1.0))
        p.drawLine(QtCore.QPointF(cx + (r + 5) * math.cos(a), cy + (r + 5) * math.sin(a)),
                   QtCore.QPointF(cx + (r + 8) * math.cos(a), cy + (r + 8) * math.sin(a)))
    if v is not None:
        col = hot if v >= warn else main
        _glow_arc(p, cx, cy, r, 135, 270 * v / 100.0, col, max(4.0, r * 0.16), 0.95)
    f = T.font("display", max(10, r * 0.42), bold=True)
    p.setFont(f); p.setPen(T.light())
    p.drawText(QtCore.QRectF(cx - r, cy - r * 0.42, 2 * r, r * 0.7), QtCore.Qt.AlignCenter, "—" if v is None else f"{v:.0f}")
    p.setFont(T.font("display", max(7, r * 0.2), spacing=1.5)); p.setPen(_c(main, 0.9))
    p.drawText(QtCore.QRectF(cx - r, cy + r * 0.22, 2 * r, r * 0.35), QtCore.Qt.AlignCenter, label)
    if sub:
        p.setFont(T.font("body", 12)); p.setPen(_c(T.light(), 0.65))
        p.drawText(QtCore.QRectF(rect.left(), cy + r + 2, rect.width(), 16), QtCore.Qt.AlignCenter, sub)
    p.restore()


def paint_weather_glyph(p, rect: QtCore.QRectF, code, t=0.0):
    """Minimal line-art weather icon from a WMO code."""
    p.save(); p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    main, light, acc = T.main(), T.light(), T.accent()
    cx, cy = rect.center().x(), rect.center().y(); s = min(rect.width(), rect.height())
    code = -1 if code is None else int(code)
    sunny = code in (0, 1); cloudy = code in (2, 3, 45, 48) or code >= 51
    if code in (0, 1, 2):
        sx, sy = (cx, cy) if code != 2 else (cx - s * 0.12, cy - s * 0.1)
        rr = s * 0.17
        p.setPen(QtGui.QPen(acc, 2.2)); p.setBrush(_c(acc, 0.18))
        p.drawEllipse(QtCore.QPointF(sx, sy), rr, rr)
        for k in range(8):
            a = math.radians(k * 45 + t * 10)
            p.drawLine(QtCore.QPointF(sx + rr * 1.35 * math.cos(a), sy + rr * 1.35 * math.sin(a)),
                       QtCore.QPointF(sx + rr * 1.75 * math.cos(a), sy + rr * 1.75 * math.sin(a)))
    if cloudy:
        path = QtGui.QPainterPath()
        bx, by, w = cx - s * 0.28, cy + s * 0.12, s * 0.56
        path.addRoundedRect(QtCore.QRectF(bx, by - s * 0.12, w, s * 0.18), s * 0.09, s * 0.09)
        path.addEllipse(QtCore.QPointF(cx - s * 0.08, by - s * 0.12), s * 0.14, s * 0.14)
        path.addEllipse(QtCore.QPointF(cx + s * 0.11, by - s * 0.08), s * 0.11, s * 0.11)
        p.setPen(QtGui.QPen(light, 2.0)); p.setBrush(_c(T.bg1(), 0.95))
        p.drawPath(path.simplified())
        if code >= 51:
            snow = code in (71, 73, 75, 77, 85, 86)
            for k in range(4):
                x = cx - s * 0.18 + k * s * 0.12
                yo = ((t * 40 + k * 7) % 12)
                if snow:
                    p.setPen(QtCore.Qt.NoPen); p.setBrush(light); p.drawEllipse(QtCore.QPointF(x, by + s * 0.14 + yo), 2.2, 2.2)
                else:
                    p.setPen(QtGui.QPen(main, 2.0)); p.drawLine(QtCore.QPointF(x, by + s * 0.1 + yo), QtCore.QPointF(x - 3, by + s * 0.18 + yo))
            if code >= 95:
                p.setPen(QtGui.QPen(acc, 2.4))
                p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(cx + 2, by + 2), QtCore.QPointF(cx - 6, by + s * 0.16),
                                                QtCore.QPointF(cx + 4, by + s * 0.16), QtCore.QPointF(cx - 4, by + s * 0.3)]))
    if code < 0:
        p.setPen(QtGui.QPen(_c(main, 0.5), 1.5)); p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(QtCore.QPointF(cx, cy), s * 0.2, s * 0.2)
    p.restore()
