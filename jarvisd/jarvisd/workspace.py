"""The J.A.R.V.I.S. workspace panel on JARVIS OS, in its own process (user unit jarvis-workspace.service).

Single instance: the panel owns the local socket `workspace` (ipc.py). `jarvisctl workspace toggle` (bound to
Ctrl+Shift+J by /usr/share/applications/jarvis-workspace.desktop, because on KDE Wayland a program can't grab a global
key itself) sends it "toggle"; if no panel is running yet it starts the unit and then sends "open".
The voice daemon does the same for "Jarvis, open/close the workspace".
"""
from __future__ import annotations
import json, logging, os, re, subprocess, sys, threading, time

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

STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "jarvis")
CFG_PATH = os.path.expanduser("~/.config/jarvis/config.json")
APP_TARGETS = {   # quick-launch tile targets -> Linux commands (tiles can also hold URLs, steam:// links and paths)
    "brave": ["flatpak", "run", "com.brave.Browser"], "chrome": ["flatpak", "run", "com.google.Chrome"],
    "spotify": ["flatpak", "run", "com.spotify.Client"], "discord": ["flatpak", "run", "com.discordapp.Discord"],
    "obs": ["flatpak", "run", "com.obsproject.Studio"], "steam": ["steam"], "files": ["dolphin"], "explorer": ["dolphin"],
    "settings": ["systemsettings"], "terminal": ["konsole"],
}

def launch_argv(target):
    """A tile's target -> argv (never a shell string)."""
    t = (target or "").strip()
    if not t:
        return None
    if t.endswith(".desktop"):
        return ["gtk-launch", os.path.basename(t)[:-8]]
    if re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I):
        return ["xdg-open", t]                          # URLs, steam://
    if t.startswith("/") or t.startswith("~"):
        p = os.path.expanduser(t)
        return [p] if os.access(p, os.X_OK) and not os.path.isdir(p) else ["xdg-open", p]
    return APP_TARGETS.get(t.lower()) or ["gtk-launch", t]

# ---------------------------------------------------------------- quick-launch tile icons
APP_ICONS = {   # tile target -> icon theme names to try (Flatpak apps export their icon under their app id)
    "brave": ["com.brave.Browser", "brave-browser"], "chrome": ["com.google.Chrome", "google-chrome"],
    "spotify": ["com.spotify.Client", "spotify-client", "spotify"], "discord": ["com.discordapp.Discord", "discord"],
    "obs": ["com.obsproject.Studio", "obs"], "steam": ["steam"], "files": ["org.kde.dolphin", "system-file-manager"],
    "explorer": ["org.kde.dolphin", "system-file-manager"], "settings": ["systemsettings", "preferences-system"],
    "terminal": ["org.kde.konsole", "utilities-terminal"],
}

def data_dirs(env=None):
    env = os.environ if env is None else env
    home = os.path.expanduser("~")
    dirs = [env.get("XDG_DATA_HOME") or os.path.join(home, ".local/share")]
    dirs += (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs += [os.path.join(home, ".local/share/flatpak/exports/share"), "/var/lib/flatpak/exports/share"]
    out = []
    for d in dirs:
        if d and d not in out:
            out.append(d)
    return out

def desktop_icon(app_id, dirs=None):
    """Icon= of an installed .desktop entry (name or path), or None."""
    name = os.path.basename(app_id)
    name = name if name.endswith(".desktop") else name + ".desktop"
    paths = [os.path.expanduser(app_id)] if app_id.startswith(("/", "~")) else []
    paths += [os.path.join(d, "applications", name) for d in (dirs or data_dirs())]
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                in_entry = False
                for line in f:
                    line = line.strip()
                    if line.startswith("["):
                        in_entry = line == "[Desktop Entry]"
                    elif in_entry and line.startswith("Icon="):
                        return line[5:].strip() or None
        except OSError:
            continue
    return None

def steam_icon(appid, home=None):
    """Steam's cached icon for an installed game (native Steam, or the Steam Flatpak), or None."""
    home = home or os.path.expanduser("~")
    roots = [os.path.join(home, p) for p in (".local/share/Steam", ".steam/steam",
                                             ".var/app/com.valvesoftware.Steam/.local/share/Steam")]
    for r in roots:
        cache = os.path.join(r, "appcache", "librarycache")
        flat = os.path.join(cache, f"{appid}_icon.jpg")
        if os.path.isfile(flat):
            return flat
        sub = os.path.join(cache, str(appid))     # newer Steam: librarycache/<appid>/<hash>.jpg (the small one is the icon)
        if os.path.isdir(sub):
            jpgs = [os.path.join(sub, f) for f in os.listdir(sub) if f.endswith(".jpg") and "/" not in f]
            jpgs = [j for j in jpgs if os.path.getsize(j) < 200_000]
            if jpgs:
                return min(jpgs, key=os.path.getsize)
    return None

def icon_candidates(entry, dirs=None, home=None):
    """A tile -> icon theme names and/or image files to try, best first (pure: no Qt). Empty = keep the letter."""
    t = (entry.get("target") or "").strip()
    if not t:
        return []
    m = re.match(r"^steam://(?:rungameid|run|launch)/(\d+)", t, re.I)
    if m:
        f = steam_icon(m.group(1), home)
        return ([f] if f else []) + ["steam"]
    if re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I):
        if t.lower().startswith(("http://", "https://")):
            if "youtube.com" in t.lower() or "youtu.be" in t.lower():
                return ["youtube", "internet-web-browser"]
            return ["internet-web-browser", "applications-internet"]
        return ["application-x-executable"]
    if t.endswith(".desktop"):
        ic = desktop_icon(t, dirs)
        return [ic] if ic else []
    if t.startswith(("/", "~")):
        p = os.path.expanduser(t)
        return ["folder"] if os.path.isdir(p) else [p + "#file"]       # "#file" = ask Qt's file-icon provider
    key = t.lower()
    names = list(APP_ICONS.get(key, []))
    argv = APP_TARGETS.get(key) or []
    app_id = argv[2] if argv[:2] == ["flatpak", "run"] else (argv[0] if argv else t)
    ic = desktop_icon(app_id, dirs)
    if ic and ic not in names:
        names.insert(0, ic)
    if t not in names:
        names.append(t)
    return names

def make_icon_for(QtGui, QtWidgets, QtCore):
    """icon_for(entry) for core.workspace.LaunchPanel: the app's real icon from the icon theme, a .desktop Icon=,
    Steam's game icon, or the file-type icon; None keeps the tile's letter."""
    prov = None
    def icon_for(entry):
        nonlocal prov
        for c in icon_candidates(entry):
            if c.endswith("#file"):
                prov = prov or QtWidgets.QFileIconProvider()
                ic = prov.icon(QtCore.QFileInfo(c[:-5]))
            elif os.path.isabs(c):
                ic = QtGui.QIcon(c) if os.path.isfile(c) else QtGui.QIcon()
            else:
                ic = QtGui.QIcon.fromTheme(c)
            if not ic.isNull():
                return ic
        return None
    return icon_for

def link_chip(cfg):
    """The top bar's Tab chip: v5.1 always said "TAB LINK READY"; JARVIS OS also runs standalone."""
    return "TAB LINK READY" if ((cfg or {}).get("forwarding") or {}).get("to") else "STANDALONE"

def detached(argv):
    from .studio import detached as d
    return d(argv)

def save_cfg(update, path=None):
    """Read-modify-write the person's config (only the keys the panel owns)."""
    path = path or CFG_PATH
    try:
        cfg = json.load(open(path))
    except (OSError, ValueError):
        cfg = {}
    update(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, path)

class VrPairLogic:
    """The workspace's PAIR VR button (rc6), GUI-free: its label from the voice daemon's vr_status answer, and what a
    click says to JARVIS (exactly what saying it does: "pair my headset" speaks the code and shows it as a notification;
    once a headset is paired, a click asks "is my headset connected")."""
    LABELS = ("PAIR VR", "VR CODE LIVE", "VR PAIRED", "VR LINKED", "VR OFF")

    @staticmethod
    def label(st):
        """-> (label, tooltip)"""
        if not st or not st.get("running", st.get("ok")):
            why = (st or {}).get("error") or "JARVIS's voice service isn't running, or VR is off in its settings"
            return "VR OFF", f"The headset bridge isn't running ({why})."
        if st.get("connected"):
            how = "your home network" if "lan" in st["connected"] else "the relay"
            return "VR LINKED", f"Your Quest is connected over {how}. Click: ask JARVIS for the headset status."
        if st.get("code_active"):
            return "VR CODE LIVE", "A pairing code is live (5 minutes): type it into JARVIS VR on the Quest. Click: a new code."
        if st.get("paired"):
            return "VR PAIRED", "A headset is paired but not connected: open JARVIS VR on the Quest. Click: headset status."
        return "PAIR VR", ("Pair your Quest with JARVIS: JARVIS says a 6-digit code and shows it as a notification; type it into "
                           "JARVIS VR (jarvis-vr.sites.tab.bot) on the headset. Same as saying \"pair my headset\".")

    @staticmethod
    def phrase(st):
        """What a click says to JARVIS, or None when the bridge isn't there."""
        if not st or not st.get("running", st.get("ok")):
            return None
        if st.get("paired") and not st.get("code_active"):
            return "is my headset connected"
        return "pair my headset"


def add_vr_button(w, QtCore, send=None, period_ms=4000):
    """Put the PAIR VR button in the workspace's top bar (left of the theme button; the status chips stop short of
    the VOICE OUT selector, so it never covers them) and keep its label current from the daemon's vr_status."""
    from .core import workspace as W
    send = send or ipc.send
    top = w.top
    lay = top.layout()
    btn = W.HudButton("PAIR VR", h=28, tip=VrPairLogic.label(None)[1])
    # wide enough for the longest label, so the bar never re-flows when it changes
    from PySide6 import QtGui
    from .core import theme as T
    fm = QtGui.QFontMetrics(T.font("display", 10, bold=True, spacing=1.5))
    btn.setFixedSize(max(fm.horizontalAdvance(x) for x in VrPairLogic.LABELS) + 24, 28)
    idx = lay.indexOf(top.b_theme)
    lay.insertWidget(idx if idx >= 0 else lay.count(), btn)
    lay.insertSpacing((idx if idx >= 0 else lay.count() - 1) + 1, 6)
    box = {"st": None, "busy": False}
    def poll():
        if box["busy"]:
            return
        box["busy"] = True
        def run():
            try:
                box["st"] = send("daemon", {"cmd": "vr_status"}, timeout=2.0)
            except Exception:
                box["st"] = None
            box["busy"] = False
        threading.Thread(target=run, daemon=True, name="vr-button-poll").start()
    def refresh():
        text, tip = VrPairLogic.label(box["st"])
        if btn.text() != text:
            btn.setText(text); btn.update()
        btn.setToolTip(tip)
    def clicked():
        said = VrPairLogic.phrase(box["st"])
        if said is None:
            if hasattr(w, "add_log"):
                w.add_log("system", VrPairLogic.label(box["st"])[1] + " (systemctl --user restart jarvisd)")
            poll(); return
        threading.Thread(target=lambda: send("daemon", {"cmd": "say", "text": said}), daemon=True, name="vr-button").start()
        QtCore.QTimer.singleShot(1500, poll)
    btn.clicked.connect(clicked)
    t = QtCore.QTimer(w); t.timeout.connect(lambda: (poll(), refresh())); t.start(period_ms)
    t2 = QtCore.QTimer(w); t2.timeout.connect(refresh); t2.start(500)
    poll()
    w._vr_button = btn; w._vr_timers = (t, t2)
    return btn


def webapps_menu_items():
    """The quick-launch panel's JARVIS APPS menu (rc6): (label, theme icon, argv) for JARVIS's web apps."""
    from . import webapps
    return [(a["name"], a["desktop"][:-8], webapps.open_argv(k)) for k, a in webapps.APPS.items()]


def add_webapps_button(w, QtWidgets, launch=None):
    """A JARVIS APPS button in the Quick launch panel's header, beside EDIT: a menu with JARVIS VR, the aim trainer and
    Reality Orbs (they open in the browser). A header button, not three more tiles: the v5.1 tile grid is sized for
    two rows, and a third row would squeeze the panels under it."""
    from PySide6 import QtGui
    from .core import workspace as W
    panel = w.p_launch
    btn = W.HudButton("JARVIS APPS", h=22, tip="JARVIS VR, the aim trainer and Reality Orbs (open in your browser)")
    menu = QtWidgets.QMenu(btn)
    for label, icon, argv in webapps_menu_items():
        act = menu.addAction(QtGui.QIcon.fromTheme(icon), label)
        act.triggered.connect(lambda _=False, argv=argv: (launch or detached)(argv))
    btn.clicked.connect(lambda: menu.popup(btn.mapToGlobal(btn.rect().bottomLeft())))
    panel.header_widgets.insert(0, btn)       # left of EDIT
    btn.setParent(panel); panel._place_header(); btn.show()
    w._webapps_button, w._webapps_menu = btn, menu
    return btn


class HudMirror:
    """Mirrors the voice daemon's hud.json into the panel: reactor state + the conversation log."""
    def __init__(self, win, read):
        self.win, self.read = win, read
        self.last = {}

    def tick(self):
        st = self.read()
        if not st:
            return
        w, last = self.win, self.last
        if st.get("state") != last.get("state"):
            w.set_state(st.get("state", "idle"))
        try: w.set_level(float(st.get("level") or 0.0))
        except Exception: pass
        if last and st.get("heard_ts") != last.get("heard_ts") and st.get("heard"):
            w.add_log("you", st["heard"])
        if last and st.get("caption_ts") != last.get("caption_ts") and st.get("caption"):
            w.add_log("jarvis", st["caption"])
        self.last = dict(st)

def build_window(cfg, QtWidgets, QtCore, real=True):
    """The v4 workspace (core/workspace.py) wired to JARVIS OS; a plain placeholder if it can't load."""
    if real:
        try:
            return _real_window(cfg, QtWidgets, QtCore)
        except Exception as e:
            log.exception("full workspace unavailable (%r); showing the basic panel", e)
    w = QtWidgets.QWidget()
    w.setWindowTitle("J.A.R.V.I.S. \u2014 Workspace")
    lay = QtWidgets.QVBoxLayout(w); lay.addWidget(QtWidgets.QLabel("J.A.R.V.I.S. workspace"))
    w.resize(900, 560)
    def _open():
        w.showNormal(); w.raise_(); w.activateWindow()
    w.open = _open
    return w

def _real_window(cfg, QtWidgets, QtCore):
    import socket
    from PySide6 import QtGui
    from .core import workspace as W, theme as T, wsdata, audioout
    from . import hud
    hud.load_fonts(QtGui, T)
    T.set_theme(cfg.get("theme", "vibranium"))
    w = W.Workspace(cfg, data_dir=os.path.join(STATE, "workspace"), hostname=socket.gethostname().split(".")[0],
                    icon_for=make_icon_for(QtGui, QtWidgets, QtCore))

    def command(text):
        if ipc.send("daemon", {"cmd": "say", "text": text}) is None:
            w.add_log("you", text)
            w.add_log("system", "JARVIS's voice service isn't running, so I couldn't do that. (systemctl --user start jarvisd)")
    w.on_command = command
    w.on_talk = lambda: ipc.send("daemon", {"cmd": "listen"})
    w.on_media = lambda a: detached(["playerctl", {"play_pause": "play-pause", "prev": "previous"}.get(a, a)])
    w.on_launch = lambda e: detached(launch_argv(e.get("target")) or ["true"])
    w.on_open = lambda p: detached(["xdg-open", p])
    def open_folder():
        fs = wsdata.clip_folders()
        if fs:
            detached(["xdg-open", fs[0]])
    w.on_open_folder = open_folder
    def theme(name):
        T.set_theme(name)
        save_cfg(lambda c: c.__setitem__("theme", name))
    w.on_theme = theme
    w.on_sounds = lambda on: save_cfg(lambda c: c.setdefault("workspace", {}).__setitem__("sounds", bool(on)))
    w.on_launch_changed = lambda tiles: save_cfg(lambda c: c.setdefault("workspace", {}).__setitem__("quick_launch", tiles))
    w.audio_devices = audioout.devices
    def audio(name):
        sink = None if audioout.is_default(name) else audioout.sink_for(name)
        if sink:
            detached(["pactl", "set-default-sink", sink])
    w.on_audio = audio
    def weather_fn():
        from .core import weather
        return weather.panel_data(cfg.get("briefing") or {})
    w.start_data(weather_fn=weather_fn, clip_folders_fn=wsdata.clip_folders, thumbs_dir=os.path.join(STATE, "workspace", "thumbs"))
    w.top.set_chip("tab", link_chip(cfg))
    try:
        add_webapps_button(w, QtWidgets)
    except Exception as e:
        logging.getLogger("jarvisd.workspace").warning("JARVIS APPS button unavailable: %r", e)
    try:
        add_vr_button(w, QtCore)
    except Exception as e:                # never let the button keep the panel from opening
        logging.getLogger("jarvisd.workspace").warning("PAIR VR button unavailable: %r", e)
    mirror = HudMirror(w, hud.read_state)
    t = QtCore.QTimer(w); t.timeout.connect(mirror.tick); t.start(150)
    w._mirror = mirror
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
