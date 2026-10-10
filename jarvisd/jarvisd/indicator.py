"""On-screen "TAB IS DRIVING" indicator + one-click kill switch (PySide6), and the JARVIS tray icon.
Runs as a user service (jarvis-indicator.service); polls the remote-control state file every 250 ms and shows a
pill on every screen while Tab is driving. The button calls the same takeback() as the voice command and
Meta+Shift+Esc. The tray menu: talk now, workspace, the offline brain (status / download) and "Link to Tab..."
(a one-time code from Tab; JARVIS works without it)."""
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

def code_ok(code):
    import re
    return bool(re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", (code or "").strip().upper()))

def tray_actions(linked, brain_status):
    """The tray menu as (label, command argv or None for a separator) - pure, so it's testable without a tray."""
    items = [("Talk to JARVIS  (Ctrl+Shift+Space)", ["jarvisctl", "listen"]),
             ("Workspace  (Ctrl+Shift+J)", ["jarvisctl", "workspace", "toggle"]), ("-", None)]
    if brain_status == "ready":
        items.append(("Offline brain: ready", None))
    elif brain_status == "installing":
        items.append(("Offline brain: downloading\u2026", None))
    else:
        items.append(("Download offline brain", ["jarvisctl", "ai", "setup"]))
    items.append(("Linked to Tab" if linked else "Link to Tab\u2026", None if linked else ["link-dialog"]))
    return items

class Tray(QtCore.QObject):
    """System tray icon with the JARVIS menu (Plasma shows it in the system tray)."""
    def __init__(self, cfg_path, ai_state, launch=None):
        super().__init__()
        import subprocess
        self.cfg_path, self.ai_state = cfg_path, ai_state
        self.launch = launch or (lambda argv: subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL,
                                                                stderr=subprocess.DEVNULL))
        icon = QtGui.QIcon.fromTheme("jarvis", QtGui.QIcon.fromTheme("audio-input-microphone"))
        self.tray = QtWidgets.QSystemTrayIcon(icon)
        self.tray.setToolTip("J.A.R.V.I.S.")
        self.menu = QtWidgets.QMenu()
        self.menu.aboutToShow.connect(self.rebuild)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda reason: reason == QtWidgets.QSystemTrayIcon.Trigger and self.launch(["jarvisctl", "workspace", "toggle"]))
        self.rebuild()
        if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def state(self):
        import json
        try:
            linked = bool((json.load(open(self.cfg_path)).get("forwarding") or {}).get("to"))
        except (OSError, ValueError):
            linked = False
        try:
            brain = json.load(open(self.ai_state)).get("status")
        except (OSError, ValueError):
            brain = None
        return linked, brain

    def rebuild(self):
        self.menu.clear()
        for label, argv in tray_actions(*self.state()):
            if label == "-":
                self.menu.addSeparator(); continue
            act = self.menu.addAction(label)
            if argv is None:
                act.setEnabled(False)
            elif argv == ["link-dialog"]:
                act.triggered.connect(self.link_dialog)
            else:
                act.triggered.connect(lambda _=False, a=argv: self.launch(a))

    def link_dialog(self, ask=None):
        ask = ask or (lambda: QtWidgets.QInputDialog.getText(None, "Link JARVIS to Tab",
                      "Text Tab \u201clink code for JARVIS OS\u201d and type the code here (like 7KQ2-M9XD-R4TB).\n"
                      "JARVIS works without Tab; linking adds the bigger jobs."))
        code, ok = ask()
        if not ok or not code.strip():
            return None
        if not code_ok(code):
            QtWidgets.QMessageBox.warning(None, "Link JARVIS to Tab", "That isn't a link code. It looks like 7KQ2-M9XD-R4TB.")
            return False
        self.launch(["jarvisctl", "link", "--code", code.strip().upper()])
        return True

def main():
    app = QtWidgets.QApplication(sys.argv)
    fonts = os.environ.get("JARVIS_FONTS", "/usr/share/fonts/jarvis")
    for f in ("Orbitron-Bold.ttf", "Rajdhani-SemiBold.ttf"):
        QtGui.QFontDatabase.addApplicationFont(os.path.join(fonts, f))
    state = os.path.join(os.path.expanduser("~"), ".local", "state", "jarvis", "remote.json")
    ind = Indicator(RemoteControl(state))
    app.setQuitOnLastWindowClosed(False)
    try:
        from . import localai
        tray = Tray(os.path.join(os.path.expanduser("~"), ".config", "jarvis", "config.json"), localai.STATE)
    except Exception as e:              # no tray on this desktop: the pill and the kill switch still work
        print("tray unavailable:", repr(e), file=sys.stderr)
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
