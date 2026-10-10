"""JARVIS OS windows for the hologram workshop, the vitals HUD, the update countdown and the 3D brain (shared design
with JARVIS for Windows v5.6). Each runs as its own process:  python -m jarvisd.holowin holo|vitals|countdown|brain
Also:  python -m jarvisd.holowin render RECIPE.json OUT.png  (a still of a model; used by tests and "save this model").
Game mode: every window minimises itself while hud.json says a game is running, and none of them takes focus from it."""
from __future__ import annotations
import argparse, datetime, json, logging, os, signal, sys, time

log = logging.getLogger("jarvisd.holowin")

def _state():
    from .workshop import STATE
    return STATE

def gpu_stats(run=None):
    """nvidia-smi first (core.hud56), then an AMD card's sysfs counters; {} when neither is there."""
    from .core import hud56
    g = hud56.gpu_stats(run) if run else hud56.gpu_stats()
    if g:
        return g
    import glob
    for dev in sorted(glob.glob("/sys/class/drm/card*/device")):
        try:
            busy = int(open(os.path.join(dev, "gpu_busy_percent")).read())
            used = int(open(os.path.join(dev, "mem_info_vram_used")).read()) / 2**20
            total = int(open(os.path.join(dev, "mem_info_vram_total")).read()) / 2**20
        except (OSError, ValueError):
            continue
        d = {"util": busy, "mem_used": used, "mem_total": total, "name": "AMD GPU"}
        for t in glob.glob(os.path.join(dev, "hwmon", "hwmon*", "temp1_input")):
            try:
                d["temp"] = int(open(t).read()) / 1000.0
            except (OSError, ValueError):
                pass
        return d
    return {}

def gaming(state=None):
    try:
        with open(os.path.join(state or _state(), "hud.json")) as f:
            return bool(json.load(f).get("game"))
    except Exception:
        return False

def _pidfile(kind, state=None):
    p = os.path.join(state or _state(), f"win_{kind}.pid")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(str(os.getpid()))
    return p

def save_model(scene, folder, canvas=None):
    """<folder>/<title>_<time>.json + .png + .obj/.mtl (for Blender). -> the .json path."""
    import re
    from .core import holo
    from .core.holoview import render_png
    os.makedirs(folder, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9]+", "_", scene.get("title", "model")).strip("_")[:40] or "model"
    stem = os.path.join(folder, f"{base}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}")
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in scene.items() if k != "lines"} | {"jarvis": "JARVIS OS hologram"}, f, indent=1, default=str)
    try:
        holo.to_obj(scene, stem + ".obj")
    except Exception:
        log.exception("obj export")
    try:
        if canvas is not None:
            canvas.grab().save(stem + ".png")
        else:
            render_png(scene, stem + ".png")
    except Exception:
        log.exception("model png")
    return stem + ".json"

def apply_view(canvas, what):
    """Same view edits as Windows v5.6 (ui56.holo_view)."""
    c = canvas
    if what == "faster": c.auto_spin = min(2.0, max(0.25, c.auto_spin) * 1.8); c.spin_speed = min(4.0, c.spin_speed * 1.6)
    elif what == "slower": c.auto_spin = max(0.05, c.auto_spin / 1.8); c.spin_speed = max(0.2, c.spin_speed / 1.6)
    elif what == "stop": c.auto_spin = 0.0; c.spin_speed = 0.0
    elif what == "spin": c.auto_spin = 0.25; c.spin_speed = 1.0
    elif what == "zoom_in": c.zoom(1.35)
    elif what == "zoom_out": c.zoom(1 / 1.35)
    elif what == "reset": c.mouseDoubleClickEvent(None)
    c._last_input = 0

class HoloFollower:
    """Polls holo.json (4 Hz) and applies new recipes / view edits / save requests to the window."""
    def __init__(self, win, state=None):
        self.win, self.state = win, state or _state()
        self.seen = 0; self.view_seen = 0; self.save_seen = 0

    def tick(self):
        from .workshop import read_json, HOLO_FILE, scene_from_recipe
        d = read_json(HOLO_FILE, self.state)
        seq = int(d.get("seq") or 0)
        if seq <= self.seen:
            return False
        r = d.get("recipe")
        if r and seq > self.seen and r != getattr(self, "_recipe", None):
            sc = scene_from_recipe(r)
            if sc is not None:
                self.win.canvas.set_scene(sc, keep_camera=bool(d.get("keep_camera")))
                self.win.setWindowTitle("J.A.R.V.I.S. \u2014 " + str(sc.get("title", "Hologram")))
                self.scene = sc
            self._recipe = r
        v = d.get("view") or {}
        if v.get("seq", 0) > self.view_seen:
            self.view_seen = v["seq"]; apply_view(self.win.canvas, v.get("what"))
        s = d.get("save") or {}
        if s.get("seq", 0) > self.save_seen and getattr(self, "scene", None) is not None:
            self.save_seen = s["seq"]
            save_model(self.scene, s.get("folder") or os.path.expanduser("~/Documents/JARVIS Models"), self.win.canvas)
        self.seen = seq
        return True

def _game_guard(win, QtCore, state=None):
    t = QtCore.QTimer(win); t.setInterval(2000)
    def tick():
        try:
            if gaming(state) and win.isVisible() and not win.isMinimized():
                win.showMinimized()
        except Exception:
            pass
    t.timeout.connect(tick); t.start()
    return t

def main(argv=None):
    ap = argparse.ArgumentParser(prog="jarvisd.holowin")
    ap.add_argument("kind", choices=["holo", "vitals", "countdown", "brain", "render"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    from PySide6 import QtCore, QtGui, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    if a.kind == "render":
        from .workshop import scene_from_recipe
        from .core.holoview import render_png
        r = json.load(open(a.args[0]))
        sc = scene_from_recipe(r)
        if sc is None:
            print("REFUSED or invalid recipe"); return 2
        render_png(sc, a.args[1], w=int(a.args[2]) if len(a.args) > 2 else 1280, h=int(a.args[3]) if len(a.args) > 3 else 800)
        print("rendered", a.args[1], sc.get("title")); return 0
    state = _state()
    pid = _pidfile(a.kind, state)
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    keep = []
    if a.kind == "holo":
        from .core.holoview import HoloWindow
        win = HoloWindow(None)
        f = HoloFollower(win, state); f.tick()
        t = QtCore.QTimer(win); t.setInterval(250); t.timeout.connect(f.tick); t.start(); keep += [f, t]
    elif a.kind == "vitals":
        from .core import hud56
        win = hud56.VitalsWindow(hud56.VitalsSampler(gpu=gpu_stats))
    elif a.kind == "countdown":
        from .core import hud56
        from .workshop import get_eta
        win = hud56.CountdownWindow(lambda: get_eta(state))
        win.place_corner()
    else:
        from .core import brainbus as bb
        from .core.brainview import BrainWindow
        from . import brainfeed
        bus = bb.BrainBus()
        for key, model in (("voice", "Piper"), ("tab", "Tab link"), ("thinker", "not on JARVIS OS"), ("eyes", "not on JARVIS OS")):
            bus.r[key].model = model
        poll = bb.Poller(bus, smi=lambda run=None: (lambda g: (g["mem_used"] * 2**20, g["mem_total"] * 2**20, g.get("name")) if g.get("mem_total") else None)(gpu_stats()))
        poll.start()
        tail = brainfeed.Tail(os.path.join(state, brainfeed.EVENTS))
        def feed_tick():
            brainfeed.replay(bus, tail.read())
            try:
                with open(os.path.join(state, "hud.json")) as fh:
                    bus.set_state(json.load(fh).get("state") or "idle")
            except Exception:
                pass
        t = QtCore.QTimer(); t.setInterval(200); t.timeout.connect(feed_tick); t.start()
        win = BrainWindow(bus)
        keep += [poll, tail, t]
    keep.append(_game_guard(win, QtCore, state))
    win.show()
    try:
        return app.exec()
    finally:
        try:
            if open(pid).read().strip() == str(os.getpid()):
                os.remove(pid)
        except OSError:
            pass

if __name__ == "__main__":
    sys.exit(main())
