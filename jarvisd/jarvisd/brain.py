"""LinuxBrain: routes a heard phrase to (1) JARVIS OS commands, (2) the shared v5.1 local commands, or
(3) Tab. Same persona as Windows: answers in the J.A.R.V.I.S. voice and calls him "sir"."""
from __future__ import annotations
import logging, os, re, subprocess

from .core import commands, qol
from .linux_hands import LinuxHands, Runner
from . import ota, rgb, switchos
from .remote_control import RemoteControl

log = logging.getLogger("jarvisd.brain")

OS_DESTRUCTIVE = {"switch_windows", "reboot_bios", "rollback", "reboot_update"}

def parse_os(low):
    """JARVIS OS-only phrases. Returns (action, extra) or None."""
    t = (low or "").strip(" .!?,").lower()
    t = re.sub(r"^(?:hey )?jarvis,? ", "", t)
    F = re.fullmatch
    if F(r"(?:take (?:back )?control(?: back)?|i(?:'ll| will) take it from here|stop tab|tab stop|kill switch|hands off(?: tab)?)", t):
        return "remote_takeback", {}
    if F(r"(?:let tab (?:drive|take over|take control)|tab,? take (?:over|control)|give tab control)(?: for (\d+) minutes?)?", t):
        m = re.search(r"(\d+) minutes?", t)
        return "remote_grant", {"minutes": int(m.group(1)) if m else 30}
    if F(r"(?:switch|go|reboot|restart|boot)(?: me)? (?:in)?to windows(?: please)?|boot windows", t):
        return "switch_windows", {}
    if F(r"(?:restart|reboot|go) (?:in)?to (?:the )?(?:bios|uefi|firmware)(?: settings)?", t):
        return "reboot_bios", {}
    if F(r"(?:check for|any|are there(?: any)?) (?:system |os )?updates\??|is there an update", t):
        return "update_check", {}
    if F(r"(?:update|upgrade) (?:jarvis os|the system|the os|yourself|everything)", t):
        return "update_stage", {}
    if F(r"(?:roll ?back|undo) (?:the )?(?:last )?(?:system |os )?update", t):
        return "rollback", {}
    m = F(r"(?:set |make |turn )?(?:the |my )?(?:rgb|lights?|lighting|leds?) (?:to |colou?r )?(\w+)", t) or \
        F(r"(?:make|turn) (?:everything|my pc|the pc) (\w+)", t)
    if m:
        word = m.group(1)
        if word in ("off", "out"):
            return "rgb", {"off": True}
        if word in rgb.COLORS:
            return "rgb", {"color": word}
        return "rgb", {"profile": word.capitalize()}
    if F(r"(?:lights|rgb|lighting) (?:off|out)|turn (?:off|out) (?:the |my )?(?:lights|rgb|lighting)", t):
        return "rgb", {"off": True}
    if F(r"(?:what(?:'s| is) )?(?:my |the )?gpu (?:temp|temperature|usage|load)|how hot is (?:my |the )?(?:gpu|graphics card)", t):
        return "gpu_stats", {}
    return None

class LinuxBrain:
    def __init__(self, cfg=None, state_dir=None, run=None, say=None, ask=None, forward=None):
        self.cfg = cfg or {}
        self.sir = self.cfg.get("address", "sir")
        self.ui = None; self.voice = None; self.pending = None
        self.state_dir = state_dir or os.path.join(os.path.expanduser("~"), ".local", "state", "jarvis")
        os.makedirs(self.state_dir, exist_ok=True)
        self.run = run or Runner()
        self._say = say or (lambda text: None)
        self.ask = ask
        self.forward = forward               # callable(text) -> sends to Tab; None = local-only
        self.spoken = []
        self.hands = LinuxHands(self.cfg, self.state_dir, run=self.run)
        self.qol = qol.QoL(self, hands=self.hands, state_dir=self.state_dir, start_clock=False)
        self.bootc = ota.Bootc(self.run)
        self.remote = RemoteControl(os.path.join(self.state_dir, "remote.json"),
                                    stop_injection=lambda: self.run(["systemctl", "--user", "stop", "ydotoold.service"]))

    def say(self, text):
        self.spoken.append(text); self._say(text); return text

    # ------------------------------------------------------------------
    def handle(self, text):
        low = commands.normalize(text)
        hit = parse_os(low)
        if hit:
            return self.os_action(*hit)
        it = qol.parse(low)
        if it is not None:
            return self.qol.handle(it, self.ask)
        if self.forward:
            self.forward(text)
            return self.say(f"Passing that to Tab, {self.sir}.")
        return self.say(f"I can't do that one offline, {self.sir}. Say \u201cwhat can you do\u201d for my local commands.")

    def _confirm(self, question):
        ans = self.ask(question) if self.ask else ""
        return bool(ans) and commands.is_yes(ans)

    def os_action(self, action, x):
        s = self.sir
        if action == "remote_takeback":
            dropped = self.remote.takeback(by="voice")
            return self.say(f"You have the controls, {s}. Tab is disconnected" + (f" and {dropped} queued actions were dropped." if dropped else "."))
        if action == "remote_grant":
            if not self._confirm(f"Let Tab drive this PC for {x['minutes']} minutes, {s}? Anything risky still asks you first."):
                return self.say(f"Very well, {s}. Tab stays hands-off.")
            self.remote.grant(x["minutes"], by="voice")
            return self.say(f"Tab is driving for {x['minutes']} minutes, {s}. Say \u201ctake back control\u201d or press Meta Shift Escape anytime.")
        if action == "switch_windows":
            code, out = self.run(["efibootmgr"])
            try:
                steps = switchos.windows_bootnext_argv(out if code == 0 else "")
            except LookupError:
                return self.say(f"I can't find Windows in the boot list, {s}. Pick it from the boot menu instead.")
            if not self._confirm(f"Restart into Windows now, {s}? Save anything open first."):
                return self.say(f"Staying here, {s}.")
            helper = ["pkexec", "/usr/libexec/jarvis-os/bootnext-windows"]
            c, o = self.run(helper)
            if c != 0:
                return self.say(f"Windows wouldn't take the one-time boot, {s}: {o.strip()[:80]}")
            self.run(steps[-1])
            return self.say(f"See you on the other side, {s}.")
        if action == "reboot_bios":
            if not self._confirm(f"Restart into the BIOS, {s}?"):
                return self.say(f"Very well, {s}.")
            self.run(["systemctl", "reboot", "--firmware-setup"])
            return self.say(f"Restarting into firmware settings, {s}.")
        if action == "update_check":
            try:
                found = self.bootc.check()
            except Exception as e:
                return self.say(f"I couldn't reach the update server, {s}.")
            return self.say(f"An update is ready, {s}. Say \u201cupdate JARVIS OS\u201d to stage it for your next restart." if found
                            else f"JARVIS OS is up to date, {s}.")
        if action == "update_stage":
            ok, out = self.bootc.stage()
            return self.say(f"Update staged, {s}. It applies on your next restart, and the old version stays in the boot menu." if ok
                            else f"The update didn't stage, {s}. Nothing changed.")
        if action == "rollback":
            if not self._confirm(f"Go back to the previous JARVIS OS version on the next restart, {s}?"):
                return self.say(f"Very well, {s}.")
            ok, _ = self.bootc.rollback()
            return self.say(f"Rollback queued for your next restart, {s}." if ok else f"Rollback failed, {s}. Nothing changed.")
        if action == "rgb":
            try:
                argv = rgb.argv_for(**x)
            except KeyError:
                return self.say(f"I don't know that colour, {s}.")
            code, out = self.run(argv)
            what = "off" if x.get("off") else x.get("color") or f"the {x.get('profile')} profile"
            return self.say(f"Lights set to {what}, {s}." if code == 0 else f"OpenRGB didn't answer, {s}. Is it running?")
        if action == "gpu_stats":
            code, out = self.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                                  "--format=csv,noheader,nounits"])
            if code != 0:
                return self.say(f"I can't read the GPU right now, {s}.")
            try:
                t, u, mu, mt = [v.strip() for v in out.strip().splitlines()[0].split(",")]
            except (IndexError, ValueError):
                return self.say(f"The GPU gave me an answer I couldn't read, {s}.")
            return self.say(f"The RTX is at {t} degrees and {u} percent load, using {round(int(mu)/1024,1)} of {round(int(mt)/1024)} gigabytes, {s}.")
        return self.say(f"I can't do that one yet, {s}.")


# ---------------------------------------------------------------------------------------------
# v2-era intents from the shared parser (volume, media, apps, time ...) executed the Linux way
# ---------------------------------------------------------------------------------------------
APPS = {   # spoken name -> argv. Flatpak IDs are Flathub's; steam:// IDs are the games' Steam app IDs.
    "steam": ["steam"], "discord": ["flatpak", "run", "com.discordapp.Discord"], "brave": ["flatpak", "run", "com.brave.Browser"],
    "obs": ["flatpak", "run", "com.obsproject.Studio"], "spotify": ["flatpak", "run", "com.spotify.Client"],
    "files": ["dolphin"], "file explorer": ["dolphin"], "terminal": ["konsole"], "settings": ["systemsettings"],
    "openrgb": ["openrgb"], "nexus": ["hyte-nexus"],
    "terraria": ["steam", "steam://rungameid/105600"], "tmodloader": ["steam", "steam://rungameid/1281930"],
    "overwatch": ["steam", "steam://rungameid/2357570"], "marvel rivals": ["steam", "steam://rungameid/2767030"],
    "vrchat": ["steam", "steam://rungameid/438100"],
}
WINDOWS_ONLY = {"rainbow six": "Rainbow Six Siege", "siege": "Rainbow Six Siege", "r6": "Rainbow Six Siege",
                "wallpaper engine": "Wallpaper Engine's editor"}
MEDIA = {"play_pause": "play-pause", "next": "next", "previous": "previous", "prev": "previous", "stop": "stop", "play": "play", "pause": "pause"}

def _execute_intent(self, it):
    s = self.sir; k = it.kind
    if k == "qol":
        return self.qol.handle(it, self.ask)
    if k == "volume":
        op, n = it.arg if isinstance(it.arg, tuple) else (it.arg, None)
        if op == "set":
            lv, _ = self.hands.master(level=n)
        else:
            cur, _ = self.hands.master()
            lv, _ = self.hands.master(level=cur + (n or 10) * (1 if op == "up" else -1))
        return self.say(f"Volume {lv} percent, {s}.")
    if k == "media":
        code, _ = self.run(["playerctl", MEDIA.get(str(it.arg), "play-pause")])
        return self.say("") if code == 0 else self.say(f"Nothing is playing, {s}.")
    if k == "open_app":
        name = str(it.arg or "").lower()
        for w, label in WINDOWS_ONLY.items():
            if w in name:
                return self.say(f"{label} only runs on Windows, {s}. Say \u201cswitch to Windows\u201d and I'll take you there.")
        argv = APPS.get(name)
        if not argv:
            argv = ["gtk-launch", name]
        code, out = self.run(argv)
        return self.say(f"Opening {name}, {s}." if code == 0 else f"I couldn't find {name}, {s}.")
    if k == "close_app":
        return self.say(f"Closing apps by voice comes in the next build, {s}.")
    if k in ("time", "date"):
        import datetime
        now = datetime.datetime.now()
        return self.say(f"It's {qol.say_clock(now, now)}, {s}." if k == "time" else f"It's {now:%A, %B} {now.day}, {s}.")
    if k == "lock":
        self.run(["loginctl", "lock-session"]); return self.say(f"Locked, {s}.")
    if k in ("sleep", "shutdown"):
        what = "Put the PC to sleep" if k == "sleep" else "Shut down the PC"
        if not self._confirm(f"{what}, {s}?"):
            return self.say(f"Very well, {s}.")
        self.run(["systemctl", "suspend" if k == "sleep" else "poweroff"])
        return self.say(f"Goodnight, {s}.")
    if k in ("youtube", "search"):
        import urllib.parse
        q = urllib.parse.quote_plus(str(it.arg or ""))
        url = f"https://www.youtube.com/results?search_query={q}" if k == "youtube" else f"https://search.brave.com/search?q={q}"
        self.hands.open_uri(url); return self.say(f"Searching, {s}.")
    if k == "help":
        return self.say(f"I handle {qol.command_count()} local commands, plus switching to Windows, updates, lights and Tab remote control, {s}.")
    if k in ("cancel", "empty"):
        return self.say(f"Very well, {s}.") if k == "cancel" else ""
    if k == "clarify":
        return self.say(str(it.arg or f"Could you say that again, {s}?"))
    if self.forward:
        self.forward(it.raw or str(it.arg))
        return self.say(f"Passing that to Tab, {s}.")
    return self.say(f"I can't do that one offline, {s}. Say \u201cwhat can you do\u201d for my local commands.")

def _handle(self, text):
    low = commands.normalize(text)
    hit = parse_os(low)
    if hit:
        return self.os_action(*hit)
    it = commands.parse(text, app_names=tuple(APPS))
    return self._execute_intent(it)

LinuxBrain._execute_intent = _execute_intent
LinuxBrain.handle = _handle
