"""On-screen "TAB IS DRIVING" indicator + one-click kill switch (PySide6). Runs as a user service
(jarvis-indicator.service); polls the remote-control state file every 250 ms and shows a pill on every screen
while Tab is driving. The button calls the same takeback() as the voice command and Meta+Shift+Esc."""
from __future__ import annotations
import os, sys, time
from PySide6 import QtCore, QtGui, QtWidgets

from .remote_control import RemoteControl

PINK, PURPLE, BG = QtGui.QColor(255, 92, 214), QtGui.QColor(176, 96, 255), QtGui.QColor(26, 11, 47, 235)

def _font(family, px, bold=False):
    f = QtGui.QFont(family); f.setPixelSize(px); f.setBold(bold); return f

class Pill(QtWidgets.QWidget):
    def __init__(self, rc, screen):
        super().__init__(None, QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
        self.rc = rc
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setWindowTitle("Tab is driving")
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(26, 12, 14, 12); lay.setSpacing(18)
        self.dot = QtWidgets.QLabel("\u25CF"); self.dot.setStyleSheet("color:#FF5CD6"); self.dot.setFont(_font("Rajdhani", 26, True))
        txt = QtWidgets.QVBoxLayout(); txt.setSpacing(0)
        t1 = QtWidgets.QLabel("TAB IS DRIVING"); t1.setFont(_font("Orbitron", 20, True)); t1.setStyleSheet("color:#FFFFFF; letter-spacing:2px")
        self.t2 = QtWidgets.QLabel("Say \u201ctake back control\u201d or press Meta+Shift+Esc")
        self.t2.setFont(_font("Rajdhani", 17, True)); self.t2.setStyleSheet("color:#ECD6FF")
        txt.addWidget(t1); txt.addWidget(self.t2)
        btn = QtWidgets.QPushButton("TAKE BACK CONTROL"); btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setFont(_font("Orbitron", 14, True))
        btn.setStyleSheet("QPushButton{background:#FF5CD6;color:#16051F;border:none;border-radius:18px;padding:10px 20px}"
                          "QPushButton:hover{background:#FF8AE3}")
        btn.clicked.connect(lambda: self.rc.takeback(by="indicator button"))
        lay.addWidget(self.dot); lay.addLayout(txt); lay.addWidget(btn)
        self.adjustSize()
        g = screen.geometry(); self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 18)
        self._blink = QtCore.QTimer(self, interval=600, timeout=self._toggle); self._blink.start()

    def _toggle(self):
        self.dot.setVisible(not self.dot.isVisible())

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setBrush(BG); p.setPen(QtGui.QPen(PINK, 2.5)); p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

class Indicator(QtCore.QObject):
    def __init__(self, rc):
        super().__init__()
        self.rc = rc; self.pills = []
        self.timer = QtCore.QTimer(self, interval=250, timeout=self.sync); self.timer.start()
        self.sync()

    def sync(self):
        on = self.rc.driving
        if on and not self.pills:
            for sc in QtGui.QGuiApplication.screens():
                p = Pill(self.rc, sc); p.show(); self.pills.append(p)
        elif not on and self.pills:
            for p in self.pills: p.close()
            self.pills = []

def main():
    app = QtWidgets.QApplication(sys.argv)
    fonts = os.environ.get("JARVIS_FONTS", "/usr/share/fonts/jarvis")
    for f in ("Orbitron-Bold.ttf", "Rajdhani-SemiBold.ttf"):
        QtGui.QFontDatabase.addApplicationFont(os.path.join(fonts, f))
    state = os.path.join(os.path.expanduser("~"), ".local", "state", "jarvis", "remote.json")
    ind = Indicator(RemoteControl(state))
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
