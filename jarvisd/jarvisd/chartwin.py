"""rc10: the chart window. `python -m jarvisd.chartwin <last.json>` shows the chart JARVIS just built in a small
always-on-top window (re-drawn at the window's size, so it stays sharp on any monitor). Esc or Close shuts it,
Save writes a PNG to ~/Pictures/JARVIS Charts. `--png OUT` renders offscreen instead (tests and the boot test).
A second chart replaces the first: the window watches its file and redraws when JARVIS writes a new one."""
from __future__ import annotations
import argparse, json, os, sys, time

from . import charts as C

def load(path):
    d = json.load(open(path))
    return C.from_json(d.get("chart") or d)

def saved_name(ch, folder):
    return os.path.join(folder, C.slug(ch) + time.strftime("-%Y%m%d-%H%M%S") + ".png")

def main(argv=None):
    ap = argparse.ArgumentParser(prog="jarvisd.chartwin")
    ap.add_argument("path")
    ap.add_argument("--png")
    ap.add_argument("--size", default="1280x744")
    a = ap.parse_args(argv)
    ch = load(a.path)
    if a.png:
        w, h = (int(x) for x in a.size.lower().split("x"))
        C.save_png(ch, a.png, w, h)
        print(a.png)
        return 0
    from PySide6 import QtCore, QtGui, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    try:
        from .core import theme as T
        T.load_fonts()
    except Exception:
        pass

    class ChartWindow(QtWidgets.QWidget):
        def __init__(self):
            super().__init__(None, QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint)
            self.setWindowTitle("JARVIS chart")
            self.setObjectName("jarvis-chart")
            self.ch = ch; self.mtime = os.path.getmtime(a.path); self.img = None
            self.setMinimumSize(560, 340)
            scr = app.primaryScreen().availableGeometry()
            w = min(1280, int(scr.width() * 0.62)); h = int(w * 0.58)
            self.resize(w, h)
            self.move(scr.x() + (scr.width() - w) // 2, scr.y() + (scr.height() - h) // 2)
            bar = QtWidgets.QHBoxLayout(); bar.addStretch(1)
            for label, fn in (("Save", self.save), ("Close", self.close)):
                b = QtWidgets.QPushButton(label); b.clicked.connect(fn); b.setCursor(QtCore.Qt.PointingHandCursor)
                b.setStyleSheet("QPushButton{color:#e9f3f5;background:rgba(47,230,214,40);border:1px solid rgba(47,230,214,150);"
                                "border-radius:6px;padding:4px 14px;font-size:12px}QPushButton:hover{background:rgba(47,230,214,90)}")
                bar.addWidget(b)
            lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(10, 8, 14, 8); lay.addLayout(bar); lay.addStretch(1)
            QtGui.QShortcut(QtGui.QKeySequence("Escape"), self, self.close)
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+S"), self, self.save)
            self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self.watch); self.timer.start(700)

        def watch(self):
            try:
                m = os.path.getmtime(a.path)
            except OSError:
                return
            if m != self.mtime:
                self.mtime = m
                try:
                    self.ch = load(a.path); self.img = None; self.update(); self.raise_(); self.activateWindow()
                except (OSError, ValueError):
                    pass

        def save(self):
            folder = C.pictures_dir(); os.makedirs(folder, exist_ok=True)
            p = C.save_png(self.ch, saved_name(self.ch, folder))
            self.setWindowTitle("JARVIS chart - saved to " + os.path.basename(p))

        def resizeEvent(self, ev):
            self.img = None; super().resizeEvent(ev)

        def paintEvent(self, ev):
            if self.img is None or self.img.size() != self.size():
                self.img = C.render(self.ch, max(200, self.width()), max(150, self.height()))
            p = QtGui.QPainter(self); p.drawImage(0, 0, self.img); p.end()

    w = ChartWindow(); w.show(); w.raise_()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
