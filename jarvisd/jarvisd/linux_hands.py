"""LinuxHands: the v5.1 `Hands` seam re-implemented for JARVIS OS (KDE Plasma 6 on Wayland, PipeWire, systemd).

Every OS call goes through `self.run(argv)` so tests can swap in a recorder. Each command is a fixed argv
(never a shell string), so a spoken phrase can never become shell code.
Tools used (all on the Bazzite KDE image unless noted): xdg-open, wpctl/pactl (PipeWire), spectacle,
qdbus (KWin / kglobalaccel / Plasma), wl-copy/wl-paste (wl-clipboard), ydotool (keys; added by our image),
playerctl (added), systemctl/loginctl, gio (trash), plasma-apply-colorscheme.
"""
from __future__ import annotations
import logging, os, re, shutil, subprocess

from .core.qol import Hands

log = logging.getLogger("jarvisd.hands")

class Runner:
    """Runs a fixed argv. Returns (returncode, stdout+stderr)."""
    def __call__(self, argv, timeout=15, input_text=None):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input_text)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except FileNotFoundError:
            return 127, f"{argv[0]}: not installed"
        except subprocess.TimeoutExpired:
            return 124, f"{argv[0]}: timed out"

# Windows key specs used by the shared core -> Linux key names (ydotool uses evdev keycodes)
EVDEV = {"ctrl": 29, "shift": 42, "alt": 56, "win": 125, "meta": 125, "enter": 28, "esc": 1, "tab": 15, "f5": 63,
         "left": 105, "right": 106, "up": 103, "down": 108, "printscreen": 99, "f4": 62, "space": 57, "delete": 111,
         "=": 13, "-": 12, "0": 11, ".": 52}
for _i, _c in enumerate("qwertyuiop"): EVDEV[_c] = 16 + _i
for _i, _c in enumerate("asdfghjkl"): EVDEV[_c] = 30 + _i
for _i, _c in enumerate("zxcvbnm"): EVDEV[_c] = 44 + _i

def ydotool_args(spec):
    """'ctrl+shift+t' -> ['29:1','42:1','20:1','20:0','42:0','29:0'] (press in order, release reversed)."""
    keys = [k.strip().lower() for k in spec.split("+") if k.strip()]
    codes = []
    for k in keys:
        if k not in EVDEV:
            raise ValueError(f"unknown key {k!r}")
        codes.append(EVDEV[k])
    return [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]

# KWin global shortcut names (kglobalaccel component "kwin") for the window/desktop ops the core asks for
KWIN = {"desktop": "Show Desktop", "snap_left": "Window Quick Tile Left", "snap_right": "Window Quick Tile Right",
        "other_monitor": "Window to Next Screen", "task_view": "Overview", "desk_next": "Switch to Next Desktop",
        "desk_prev": "Switch to Previous Desktop", "minimize": "Window Minimize", "maximize": "Window Maximize",
        "close": "Window Close", "switch": "Walk Through Windows"}

# Windows settings pages the core may open -> KDE System Settings modules
SETTINGS = {"ms-settings:sound": "kcm_pulseaudio", "ms-settings:display": "kcm_kscreen", "ms-settings:bluetooth": "kcm_bluetooth",
            "ms-settings:network": "kcm_networkmanagement", "ms-settings:personalization": "kcm_lookandfeel",
            "ms-settings:privacy-microphone": "kcm_pulseaudio", "ms-settings:windowsupdate": "kcm_updates",
            "ms-settings:apps-volume": "kcm_pulseaudio", "ms-settings:notifications": "kcm_notifications"}

class LinuxHands(Hands):
    def __init__(self, cfg=None, data_dir=None, run=None):
        super().__init__(cfg, data_dir)
        self.run = run or Runner()

    def _ok(self, argv, **kw):
        code, out = self.run(argv, **kw)
        if code != 0:
            raise RuntimeError(out.strip()[:300] or f"exit {code}")
        return out

    # ---------- opening things ----------
    def open_uri(self, target):
        for cand in target.split("|"):
            if cand.startswith("https://"):
                return self.run(["xdg-open", cand])[0] == 0
            if cand in SETTINGS:
                return self.run(["systemsettings", SETTINGS[cand]])[0] == 0
            if cand.endswith(".exe") or cand.startswith(("shell:", "ms-")):
                continue                       # Windows-only targets have no Linux meaning
            if self.run(["xdg-open", cand])[0] == 0:
                return True
        return False

    def open_path(self, path):
        return self.run(["xdg-open", path])[0] == 0

    def reveal(self, path):
        """Show a file selected in Dolphin (never runs it)."""
        return self.run(["dolphin", "--select", os.path.normpath(path)])[0] == 0

    def known_folder(self, name):
        home = os.path.expanduser("~")
        xdg = {"Downloads": "DOWNLOAD", "Documents": "DOCUMENTS", "Desktop": "DESKTOP", "Pictures": "PICTURES",
               "Videos": "VIDEOS", "Music": "MUSIC"}
        if name == "Home": return home
        if name == "AppData": return os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config"))
        if name == "JARVIS": return os.path.dirname(os.path.abspath(__file__))
        if name == "JARVIS data": return self.data_dir or os.path.join(os.environ.get("XDG_STATE_HOME", os.path.join(home, ".local", "state")), "jarvis")
        if name == "Screenshots": return os.path.join(self.known_folder("Pictures"), "Screenshots")
        if name == "Captures": return os.path.join(self.known_folder("Videos"), "Captures")
        if name in xdg:
            code, out = self.run(["xdg-user-dir", xdg[name]])
            if code == 0 and out.strip().startswith("/"):
                return out.strip().splitlines()[0]
        return os.path.join(home, name)

    # ---------- keyboard / clipboard ----------
    def keys(self, spec):
        self._ok(["ydotool", "key", *ydotool_args(spec)]); return True

    def type_text(self, text):
        self._ok(["ydotool", "type", "--", text]); return True

    def clip_get(self):
        code, out = self.run(["wl-paste", "--list-types"])
        types = out.split() if code == 0 else []
        if "text/uri-list" in types: return "\x00files"
        if any(t.startswith("image/") for t in types) and not any(t.startswith("text/plain") for t in types): return "\x00image"
        code, out = self.run(["wl-paste", "--no-newline", "--type", "text/plain"])
        return out if code == 0 else ""

    def clip_clear(self):
        self._ok(["wl-copy", "--clear"])
        self.run(["qdbus", "org.kde.klipper", "/klipper", "org.kde.klipper.klipper.clearClipboardHistory"])
        return True

    # ---------- windows / desktops ----------
    def window(self, op):
        if op not in KWIN:
            raise RuntimeError(f"no KWin shortcut for {op}")
        self._ok(["qdbus", "org.kde.kglobalaccel", "/component/kwin", "org.kde.kglobalaccel.Component.invokeShortcut", KWIN[op]])
        return True

    # ---------- sound (PipeWire) ----------
    def master(self, level=None, mute=None):
        if level is not None:
            self._ok(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{max(0, min(100, int(level)))}%"])
        if mute is not None:
            self._ok(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"])
        out = self._ok(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        m = re.search(r"Volume:\s*([\d.]+)(\s*\[MUTED\])?", out)
        if not m:
            raise RuntimeError(out.strip()[:200])
        return int(round(float(m.group(1)) * 100)), bool(m.group(2))

    def app_volume(self, app, level=None, mute=None):
        """Per-app volume via pactl sink-inputs whose application.name / binary matches `app`."""
        out = self._ok(["pactl", "list", "sink-inputs"])
        hits = []
        for block in out.split("Sink Input #")[1:]:
            idx = block.split()[0]
            names = " ".join(re.findall(r'application\.(?:name|process\.binary) = "([^"]+)"', block)).lower()
            if app.lower() in names:
                hits.append(idx)
        for idx in hits:
            if level is not None: self._ok(["pactl", "set-sink-input-volume", idx, f"{int(level)}%"])
            if mute is not None: self._ok(["pactl", "set-sink-input-mute", idx, "1" if mute else "0"])
        return len(hits)

    def mute_key(self):
        self._ok(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"]); return True

    # ---------- power (the core always confirms these first) ----------
    POWER = {"restart_pc": ["systemctl", "reboot"], "sign_out": ["qdbus", "org.kde.Shutdown", "/Shutdown", "logout"],
             "hibernate": ["systemctl", "hibernate"], "abort_shutdown": ["shutdown", "-c"]}
    def power(self, op):
        code, out = self.run(self.POWER[op])
        return code == 0, out.strip()

    # ---------- trash ----------
    def bin_count(self):
        files = os.path.join(os.path.expanduser("~"), ".local", "share", "Trash", "files")
        if not os.path.isdir(files):
            return 0, 0
        n = size = 0
        for root, dirs, fs in os.walk(files):
            for f in fs:
                n += 1 if root == files else 0
                try: size += os.path.getsize(os.path.join(root, f))
                except OSError: pass
            if root == files: n += len(dirs)
        return n, size

    def empty_bin(self):
        return self.run(["gio", "trash", "--empty"])[0] == 0

    # ---------- look ----------
    def theme_mode(self, mode):
        scheme = "BreezeLight" if mode == "light" else "JarvisVibranium"
        self._ok(["plasma-apply-colorscheme", scheme]); return True

    def screenshot(self):
        folder = self.known_folder("Screenshots"); os.makedirs(folder, exist_ok=True)
        self._ok(["spectacle", "--background", "--nonotify", "--fullscreen", "--output",
                  os.path.join(folder, "JARVIS-%Y%m%d-%H%M%S.png")]); return True

    def snip(self):
        self._ok(["spectacle", "--region"]); return True

    def now_playing(self):
        code, out = self.run(["playerctl", "metadata", "--format", "{{artist}} - {{title}}"])
        return out.strip() if code == 0 and out.strip() else None
