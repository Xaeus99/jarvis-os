"""v5: a "what's new" card shown once on the first launch after the update."""
from __future__ import annotations
from PySide6 import QtCore, QtGui, QtWidgets
from . import theme as T

LINES = [
    ("90+ new things I do myself", "Timers, alarms, reminders, a stopwatch, exact volume, screenshots, files, CPU / RAM / disk, clipboard, windows."),
    ("Timers & alarms", "\u201cSet a pizza timer for 12 minutes\u201d \u00b7 \u201cWake me up at 7\u201d \u00b7 \u201cRemind me in 20 minutes to\u2026\u201d"),
    ("Your PC, by voice", "\u201cTake a screenshot\u201d \u00b7 \u201cOpen my latest download\u201d \u00b7 \u201cFind my homework file\u201d \u00b7 \u201cSnap left\u201d"),
    ("Quick answers", "\u201cHow much RAM am I using?\u201d \u00b7 \u201cAm I online?\u201d \u00b7 \u201cWhat\u2019s 15 percent of 80?\u201d \u00b7 \u201cRoll a d20\u201d"),
    ("Safe by default", "Restart, sign out, hibernate and emptying the Recycle Bin always ask you first."),
    ("Tab's progress", "\u201cWhat are you working on?\u201d shows Tab's tasks with progress, ETA and pictures you can open full screen."),
]

class WhatsNewCard(QtWidgets.QWidget):
    def __init__(self, version="5.1.0", seconds=45, parent=None):
        super().__init__(parent, QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
        self.setWindowTitle("J.A.R.V.I.S. — what's new")
        self.version = version
        self.resize(560, 505)
        self.btn = QtWidgets.QPushButton("Got it, JARVIS", self)
        self.btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn.clicked.connect(self.close)
        self.btn.setGeometry(self.width() - 190, self.height() - 58, 165, 36)
        m = T.main()
        self.btn.setStyleSheet("QPushButton{color:#ffffff;background:%s;border:1px solid %s;border-radius:8px;font-family:'%s';font-size:15px;font-weight:600;}"
                               "QPushButton:hover{background:%s;}" % (T.rgba(m, 110), T.rgba(m, 220), T.BODY, T.rgba(m, 170)))
        if seconds:
            QtCore.QTimer.singleShot(int(seconds * 1000), self.close)

    def place(self):
        scr = QtGui.QGuiApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

    def mousePressEvent(self, e):
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", None) is not None and e.buttons() & QtCore.Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def paintEvent(self, ev):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        g = QtGui.QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, T.bg1()); g.setColorAt(1, T.bg0())
        p.setBrush(g); p.setPen(QtGui.QPen(T.main(), 1.6)); p.drawRoundedRect(r, 16, 16)
        # corner brackets
        pen = QtGui.QPen(T.light(), 2); p.setPen(pen); L = 22; q = r.adjusted(6, 6, -6, -6)
        for (x, y, dx, dy) in ((q.left(), q.top(), 1, 1), (q.right(), q.top(), -1, 1), (q.left(), q.bottom(), 1, -1), (q.right(), q.bottom(), -1, -1)):
            p.drawLine(QtCore.QPointF(x, y), QtCore.QPointF(x + dx * L, y)); p.drawLine(QtCore.QPointF(x, y), QtCore.QPointF(x, y + dy * L))
        p.setPen(T.accent()); p.setFont(T.font("display", 11, bold=True, spacing=3))
        p.drawText(QtCore.QRectF(28, 22, 500, 20), QtCore.Qt.AlignLeft, "SYSTEM UPGRADE COMPLETE")
        p.setPen(QtGui.QColor("#ffffff")); p.setFont(T.font("display", 22, bold=True, spacing=2))
        p.drawText(QtCore.QRectF(28, 44, 500, 36), QtCore.Qt.AlignLeft, getattr(self, "title", None) or f"J.A.R.V.I.S. MK V  ·  v{self.version}")
        p.setPen(T.light()); p.setFont(T.font("body", 14))
        p.drawText(QtCore.QRectF(28, 82, 500, 22), QtCore.Qt.AlignLeft, getattr(self, "subtitle", None) or ("The quality-of-life update, sir. Less finicky, more listening." if self.version.startswith("5.0") else "More I can do myself, sir. Less waiting on Tab."))
        y = 118
        for title, body in getattr(self, "lines", None) or LINES:
            p.setPen(QtCore.Qt.NoPen); p.setBrush(T.main()); p.drawEllipse(QtCore.QPointF(36, y + 10), 4, 4)
            p.setPen(T.main()); p.setFont(T.font("display", 11, bold=True, spacing=1.5))
            p.drawText(QtCore.QRectF(50, y, 470, 18), QtCore.Qt.AlignLeft, title.upper())
            p.setPen(QtGui.QColor("#e9f8ff")); p.setFont(T.font("body", 14))
            p.drawText(QtCore.QRectF(50, y + 18, 480, 40), QtCore.Qt.AlignLeft | QtCore.Qt.TextWordWrap, body)
            y += 52
        p.setPen(QtGui.QColor("#8aa0b0")); p.setFont(T.font("body", 12))
        p.drawText(QtCore.QRectF(28, self.height() - 52, 330, 30), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "Say \u201cJarvis, what can you do?\u201d or \u201chelp with timers\u201d.")
        p.end()
