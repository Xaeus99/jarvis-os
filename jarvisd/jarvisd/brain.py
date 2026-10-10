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
# the shared v5.1 command kinds JARVIS OS runs locally (everything else the shared parser knows goes to Tab)
LOCAL_KINDS = {"workspace_open", "workspace_close", "stream_start", "stream_stop", "record_start", "record_stop", "clip",
               "learn_face", "forget_face", "camera_on", "camera_off"}

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
    m = F(r"(?:turn |switch )?(?:on |off )?(game|gaming|stream|streaming) mode(?: (on|off))?|(?:start|end|stop|exit|leave|enter) (game|gaming|stream|streaming) mode", t)
    if m:
        kind = "stream" if "stream" in t else "game"
        off = bool(re.search(r"\b(off|end|stop|exit|leave)\b", t))
        return "mode", {"kind": kind, "on": not off}
    if F(r"(?:run a |do a )?(?:gaming|game) (?:check|checkup|status)|check my (?:gaming )?(?:setup|rig|pc for gaming)|is my pc ready (?:for|to) (?:game|gaming|stream|streaming)", t):
        return "gaming_check", {}
    if F(r"(?:set up|fix|optimi[sz]e) my (?:monitor|display|screen)s?|(?:max|maximum|highest) refresh(?: rate)?|turn on (?:vrr|adaptive sync|g-?sync|freesync)", t):
        return "display_setup", {"hdr": None}
    if F(r"turn (?:on|off) hdr|hdr (?:on|off)", t):
        return "display_setup", {"hdr": "on" in t}
    if F(r"(?:restore|reinstall|bring back|download|install|get) (?:all )?(?:of )?my (?:steam )?games(?: from windows)?", t):
        return "games_restore", {}
    if F(r"(?:install|download|get|set up|reinstall) (?:all )?(?:of )?my apps", t):
        return "apps_install", {}
    if F(r"(?:what(?:'s| is) )?(?:my |the )?gpu (?:temp|temperature|usage|load)|how hot is (?:my |the )?(?:gpu|graphics card)", t):
        return "gpu_stats", {}
    m = F(r"(?:switch|change|go|cut|flip)(?: over)? (?:the )?(?:obs )?(?:scene )?to (?:the |my )?(.+?) scene", t) or \
        F(r"(?:switch|change|set) (?:the )?(?:obs )?scene to (?:the |my )?(.+)", t) or F(r"(?:obs )?scene (.+)", t)
    if m:
        return "obs_scene", {"name": m.group(1)}
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

    def launch(self, argv):
        """Start an app and don't wait for it (a real Runner would kill it at its timeout); tests record the argv."""
        if isinstance(self.run, Runner):
            from .studio import detached
            return detached(argv)
        return self.run(argv)

    @property
    def studio(self):
        if getattr(self, "_studio", None) is None:
            from . import gaming
            from .studio import Studio
            self._studio = Studio(self.cfg, launch=self.launch, run=self.run,
                                  set_mode=lambda kind, on: gaming.set_mode(self.run, kind, on, self.cfg.get("gaming", {})))
        return self._studio

    @studio.setter
    def studio(self, v):
        self._studio = v

    def panel(self, what):
        """Open / close / toggle the workspace panel process (single instance, see workspace.py)."""
        from . import workspace
        return workspace.control(what, run=self.run)

    def local_action(self, it):
        s, k = self.sir, it.kind
        if k in ("workspace_open", "workspace_close"):
            opening = k == "workspace_open"
            try:
                vis, how = self.panel("open" if opening else "close")
            except Exception as e:
                log.info("workspace %s failed: %r", k, e); vis, how = None, "failed"
            if opening and how == "failed":
                return self.say(f"I couldn't bring up the workspace, {s}. Ctrl Shift J should do it.")
            if opening:
                return self.say(f"Workspace online, {s}.")
            return self.say(f"Workspace closed, {s}. I'm still listening.")
        if k in ("stream_start", "record_start"):
            return self.say(self.studio.start("record" if k == "record_start" else "stream", s))
        if k in ("stream_stop", "record_stop"):
            return self.say(self.studio.stop("record" if k == "record_stop" else "stream", s))
        if k == "clip":
            return self.say(self.studio.clip(s))
        if k in ("learn_face", "forget_face", "camera_on", "camera_off"):
            return self.camera_action(k)
        return self.say(f"That one isn't available on Linux yet, {s}.")

    def camera_action(self, k):
        s, cam = self.sir, getattr(self, "camera", None)
        if cam is None:
            from . import camera as camglue
            if not camglue.available():
                return self.say(f"The camera greeting isn't available on this system yet, {s}: the vision add-on is missing.")
            if k == "camera_off":
                return self.say(f"Camera greeting is already off, {s}.")
            return self.say(f"I don't have a camera to look through yet, {s}. Plug in a webcam and ask me again."
                            if not camglue.has_device() else f"The camera greeting is switched off in my settings, {s}.")
        if k == "learn_face":
            self.say(f"Look straight at the camera and hold still for about ten seconds, {s}.")
            ok, msg = cam.enroll()
            return self.say(msg)
        if k == "forget_face":
            cam.book.forget()
            return self.say(f"Done, {s}. Your face data is deleted; I'll greet anyone at this desk again.")
        on = k == "camera_on"
        (cam.enabled.set if on else cam.enabled.clear)()
        return self.say(f"Camera greeting {'on' if on else 'off'}, {s}.")

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
        if action == "obs_scene":
            return self.say(self.studio.scene(x["name"], s))
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
        if action == "mode":
            from . import gaming
            ok, failed = gaming.set_mode(self.run, x["kind"], x["on"], self.cfg.get("gaming", {}))
            name = "Stream mode" if x["kind"] == "stream" else "Game mode"
            if not x["on"]:
                return self.say(f"Back to normal, {s}." + (f" These didn't answer: {', '.join(failed)}." if failed else ""))
            extra = " Audio latency is down, notifications are held and indexing is paused." if x["kind"] == "stream" else ""
            return self.say(f"{name} on, {s}. Performance power profile.{extra}" + (f" These didn't answer: {', '.join(failed)}." if failed else ""))
        if action == "gaming_check":
            from . import gaming
            lines = gaming.gaming_check(self.run)
            return self.say(f"Gaming check, {s}: " + "; ".join(f"{k}: {v}" for k, v in lines) + ".")
        if action == "display_setup":
            from . import gaming
            code, out = self.run(["kscreen-doctor", "-j"])
            if code != 0:
                return self.say(f"I can't read the display settings, {s}.")
            cmds = gaming.best_display_commands(out, hdr=x.get("hdr"))
            if not cmds:
                return self.say(f"I don't see a connected display, {s}.")
            code, _ = self.run(["kscreen-doctor", *cmds])
            return self.say(f"Done, {s}: native resolution, highest refresh rate, adaptive sync on." + {True: " HDR is on.", False: " HDR is off."}.get(x.get("hdr"), "")
                            if code == 0 else f"The display didn't take the change, {s}. Nothing was saved.")
        if action == "games_restore":
            if not self._confirm(f"Shall I queue your Windows Steam games for download here, {s}? I only read the Windows drive, "
                                 f"and Steam needs to be closed while I do it."):
                return self.say(f"Very well, {s}.")
            self.run(["steam", "-shutdown"])
            code, _ = self.run(["systemd-run", "--user", "--collect", "--unit=jarvis-games-restore", "--on-active=5",
                                "/usr/bin/jarvisctl", "games", "restore"])
            return self.say(f"On it, {s}. Open Steam in a minute and your games will be downloading. "
                            f"Anything that only runs on Windows stays there." if code == 0 else f"I couldn't start the restore, {s}.")
        if action == "apps_install":
            code, _ = self.run(["systemd-run", "--user", "--collect", "--unit=jarvis-apps-install",
                                "/usr/bin/jarvisctl", "apps", "install"])
            return self.say(f"Installing your apps in the background, {s}. I'll leave a notification for each." if code == 0
                            else f"I couldn't start the app install, {s}.")
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
    if k in LOCAL_KINDS:
        return self.local_action(it)
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
        code, out = self.launch(argv)
        return self.say(f"Opening {name}, {s}." if code == 0 else f"I couldn't find {name}, {s}.")
    if k == "close_app":
        return self.say(f"Closing apps by voice comes in the next build, {s}.")
    if k == "weather":
        line = self.weather_line(tomorrow="tomorrow" in (it.raw or "").lower())
        if line:
            return self.say(line)
        if self.forward:                      # no city on this PC yet: Tab can still answer
            self.forward(it.raw or "what's the weather")
            return self.say(f"I don't have your city on this system yet, {s}, so I've asked Tab.")
        return self.say(f"I don't know your city yet, {s}. Run `jarvisctl link --from-windows`, or put it in the config under briefing.")
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

def _weather_line(self, tomorrow=False):
    """Same as Windows: Open-Meteo for the city in config["briefing"] (only its coordinates are sent). None if no city."""
    from .core import weather
    try:
        return weather.spoken(self.cfg.get("briefing") or {}, tomorrow=tomorrow)
    except Exception as e:
        log.info("weather: %r", e)
        return f"I couldn't reach the weather service just now, {self.sir}."

LinuxBrain.weather_line = _weather_line
LinuxBrain._execute_intent = _execute_intent
LinuxBrain.handle = _handle
