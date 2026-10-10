"""The J.A.R.V.I.S. HUD on JARVIS OS: the same ring as Windows (core/overlay.py, core/theme.py), in its own process.

jarvisd (the voice daemon) publishes what JARVIS is doing to ~/.local/state/jarvis/hud.json; this process (user
service jarvis-hud.service) polls it 10 times a second and pops the ring up while JARVIS listens, thinks or speaks,
then hides it a few seconds after. It runs through XWayland (QT_QPA_PLATFORM=xcb in the unit) because plain Wayland
clients can't place themselves in a screen corner or stay on top.
"""
from __future__ import annotations
import json, logging, os, sys, time

log = logging.getLogger("jarvisd.hud")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "jarvis")
HUD_FILE = os.path.join(STATE, "hud.json")
FONT_DIRS = ("/usr/share/fonts/jarvis", os.path.join(os.path.dirname(__file__), "..", "..", "image", "system_files", "usr", "share", "fonts", "jarvis"))

class HudBus:
    """Writer side (in jarvisd). Atomic writes, so the HUD never reads half a file."""
    def __init__(self, path=HUD_FILE, clock=time.time):
        self.path, self.clock = path, clock
        self.st = {"state": "idle", "caption": "", "heard": "", "heard_status": "heard", "level": 0.0, "theme": None}

    def publish(self, **kw):
        self.st.update(kw); self.st["ts"] = self.clock()
        if "heard" in kw: self.st["heard_ts"] = self.st["ts"]
        if "caption" in kw: self.st["caption_ts"] = self.st["ts"]
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.st, f)
        os.replace(tmp, self.path)

    # convenience hooks for the daemon
    def listening(self): self.publish(state="listening")
    def thinking(self, heard=""): self.publish(state="thinking", heard=heard, heard_status="heard")
    def speaking(self, text): self.publish(state="speaking", caption=text)
    def idle(self): self.publish(state="idle")

def read_state(path=HUD_FILE):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def load_fonts(QtGui, T):
    T.load_fonts()
    fams = set()
    for d in FONT_DIRS:
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".ttf"):
                    fid = QtGui.QFontDatabase.addApplicationFont(os.path.join(d, f))
                    fams.update(QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else [])
    fams.update(QtGui.QFontDatabase.families())
    if "Orbitron" in fams: T.DISPLAY = "Orbitron"
    if "Rajdhani" in fams: T.BODY = "Rajdhani"

class Follower:
    """Applies hud.json to the overlay; decides when the ring is visible (pure logic, tested without a screen)."""
    def __init__(self, hud, cfg=None, clock=time.time):
        self.hud, self.clock = hud, clock
        c = cfg or {}
        self.idle_hide = float(c.get("idle_hide_seconds", 4))
        self.always = bool(c.get("always_show_on_desktop", False))
        self.last = {}; self.active_until = 0.0

    def tick(self, st):
        now = self.clock()
        if st:
            if st.get("state") != self.last.get("state"):
                self.hud.set_state(st.get("state", "idle"))
            if st.get("caption_ts") != self.last.get("caption_ts") and st.get("caption"):
                self.hud.set_caption(st["caption"], 6)
            if st.get("heard_ts") != self.last.get("heard_ts") and st.get("heard"):
                self.hud.set_heard(st["heard"], st.get("heard_status", "heard"))
            self.hud.set_level(float(st.get("level") or 0.0))
            if st.get("state") in ("listening", "thinking", "speaking"):
                self.active_until = now + self.idle_hide
            self.last = dict(st)
        want = self.always or now < self.active_until
        if want and not self.hud.isVisible():
            self.hud.show()
        elif not want and self.hud.isVisible():
            self.hud.hide()
        return want

def main(argv=None):
    logging.basicConfig(level=logging.INFO)
    from PySide6 import QtCore, QtGui, QtWidgets
    from .core import theme as T, overlay
    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    cfg_path = os.path.expanduser("~/.config/jarvis/config.json")
    cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    load_fonts(QtGui, T)
    T.set_theme(cfg.get("theme", "vibranium"))
    hud = overlay.Overlay({**cfg.get("overlay", {}), **cfg.get("hud", {})})
    fol = Follower(hud, cfg.get("hud", {}))
    timer = QtCore.QTimer(); timer.timeout.connect(lambda: fol.tick(read_state())); timer.start(100)
    return app.exec()

def render_png(path, state="speaking", caption="", heard="", theme="vibranium", size=320):
    """Offscreen render of the real HUD window (for tests and screenshots)."""
    from PySide6 import QtCore, QtGui, QtWidgets
    from .core import theme as T, overlay
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    load_fonts(QtGui, T); T.set_theme(theme)
    hud = overlay.Overlay({"size": size})
    hud.set_state(state)
    if caption: hud.set_caption(caption)
    if heard: hud.set_heard(heard)
    img = QtGui.QImage(hud.size(), QtGui.QImage.Format_ARGB32); img.fill(QtCore.Qt.transparent)
    hud.render(img); img.save(path)
    return img

if __name__ == "__main__":
    sys.exit(main())
