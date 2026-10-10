"""The J.A.R.V.I.S. workspace panel on JARVIS OS, in its own process (user unit jarvis-workspace.service).

Single instance: the panel owns the local socket `workspace` (ipc.py). `jarvisctl workspace toggle` (bound to
Ctrl+Shift+J by /usr/share/applications/jarvis-workspace.desktop, because on KDE Wayland a program can't grab a global
key itself) sends it "toggle"; if no panel is running yet it starts the unit and then sends "open".
The voice daemon does the same for "Jarvis, open/close the workspace".
"""
from __future__ import annotations
import json, logging, os, subprocess, sys, threading, time

from . import ipc

log = logging.getLogger("jarvisd.workspace")
NAME = "workspace"
SERVICE = "jarvis-workspace.service"
COMMANDS = ("toggle", "open", "close", "status")

def control(what="toggle", run=None, send=ipc.send, wait_s=10.0, sleep=time.sleep, spawn=None):
    """Client side. Returns (visible_or_None, how): how is 'sent', 'started', 'not running' or 'failed'."""
    if what not in COMMANDS:
        raise ValueError(what)
    r = send(NAME, {"cmd": what})
    if r is not None:
        return r.get("visible"), "sent"
    if what in ("close", "status"):
        return False, "not running"
    rc = 1
    if run is not None:
        rc, _ = run(["systemctl", "--user", "start", SERVICE], timeout=15)
    if rc != 0:                                   # no systemd user session (or no unit): start it directly
        (spawn or _spawn)()
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        sleep(0.25)
        r = send(NAME, {"cmd": "open"})
        if r is not None:
            return r.get("visible", True), "started"
    return None, "failed"

def _spawn():
    env = dict(os.environ); env.setdefault("QT_QPA_PLATFORM", "xcb")
    subprocess.Popen([sys.executable, "-m", "jarvisd.workspace"], env=env, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class Controller:
    """Server side, GUI-free logic: maps a message to window calls. `call(fn)` runs fn on the GUI thread and
    returns its result (in tests: directly)."""
    def __init__(self, win, call=None):
        self.win = win
        self.call = call or (lambda fn: fn())

    def visible(self):
        w = self.win
        return bool(w.isVisible() and not w.isMinimized())

    def handle(self, msg):
        cmd = (msg or {}).get("cmd")
        w = self.win
        if cmd == "toggle":
            self.call(lambda: w.close() if self.visible() else w.open())
        elif cmd == "open":
            self.call(w.open)
        elif cmd == "close":
            self.call(lambda: w.close() if w.isVisible() else None)
        elif cmd == "log" and hasattr(w, "add_log"):
            self.call(lambda: w.add_log(msg.get("who", "jarvis"), msg.get("text", "")))
        elif cmd == "status":
            pass
        else:
            return {"ok": False, "error": f"unknown command {cmd!r}"}
        return {"ok": True, "visible": self.call(self.visible)}

def make_gui_caller(QtCore):
    """A callable that runs fn on the Qt GUI thread and waits (max 3 s) for its result."""
    class Bridge(QtCore.QObject):
        job = QtCore.Signal(object)
    b = Bridge()
    def run_job(box):
        try: box["r"] = box["fn"]()
        except Exception as e: box["e"] = e
        box["done"].set()
    b.job.connect(run_job, QtCore.Qt.QueuedConnection)
    gui = QtCore.QThread.currentThread()
    def call(fn):
        if QtCore.QThread.currentThread() == gui:
            return fn()
        box = {"fn": fn, "done": threading.Event()}
        b.job.emit(box)
        box["done"].wait(3.0)
        if "e" in box: raise box["e"]
        return box.get("r")
    call.bridge = b                       # keep it alive
    return call

def build_window(cfg, QtWidgets, QtCore):
    """The workspace window. A plain placeholder until the full v4 workspace is ported."""
    w = QtWidgets.QWidget()
    w.setWindowTitle("J.A.R.V.I.S. \u2014 Workspace")
    lay = QtWidgets.QVBoxLayout(w); lay.addWidget(QtWidgets.QLabel("J.A.R.V.I.S. workspace"))
    w.resize(900, 560)
    def _open():
        w.showNormal(); w.raise_(); w.activateWindow()
    w.open = _open
    return w

def load_cfg():
    p = os.path.expanduser("~/.config/jarvis/config.json")
    try:
        return json.load(open(p))
    except Exception:
        return {}

def main(argv=None):
    logging.basicConfig(level=logging.INFO)
    argv = sys.argv[1:] if argv is None else argv
    if ipc.alive(NAME):                   # a second start just toggles the running panel
        ipc.send(NAME, {"cmd": "open" if "--open" in argv else "toggle"})
        return 0
    from PySide6 import QtCore, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)  # closing the window hides it; the process waits for the next toggle
    app.setApplicationName("jarvis-workspace"); app.setDesktopFileName("jarvis-workspace")
    win = build_window(load_cfg(), QtWidgets, QtCore)
    ctl = Controller(win, make_gui_caller(QtCore))
    srv = ipc.Server(NAME, ctl.handle).start()
    app.aboutToQuit.connect(srv.close)
    if "--open" in argv:
        win.open()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
