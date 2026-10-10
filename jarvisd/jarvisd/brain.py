"""LinuxBrain: routes a heard phrase to (1) JARVIS OS commands, (2) the shared v5.1 local commands, or
(3) Tab. Same persona as Windows: answers in the J.A.R.V.I.S. voice and calls him "sir"."""
from __future__ import annotations
import logging, os, re, subprocess

from .core import commands, qol
from .linux_hands import LinuxHands, Runner
from . import makers, ota, rgb, switchos, workshop
from .remote_control import RemoteControl

log = logging.getLogger("jarvisd.brain")

OS_DESTRUCTIVE = {"switch_windows", "reboot_bios", "rollback", "reboot_update"}
# the shared v5.1 command kinds JARVIS OS runs locally (everything else the shared parser knows goes to Tab)
LOCAL_KINDS = {"workspace_open", "workspace_close", "stream_start", "stream_stop", "record_start", "record_stop", "clip",
               "learn_face", "forget_face", "camera_on", "camera_off", "open_clips", "clips_folder",
               "copilot", "copilot_ask", "copilot_open", "audio_mirror"}
NOT_ON_LINUX = {"copilot": "Copilot", "copilot_ask": "Copilot", "copilot_open": "Copilot", "audio_mirror": "Audio mirroring"}

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
    if F(r"what(?:'s| is| has) new(?: (?:in|with) (?:jarvis(?: os)?|you|the system|this update))?|(?:show (?:me )?)?what'?s new|what changed(?: in the update)?", t):
        return "whats_new", {}
    if F(r"(?:please )?(?:download|install|set up|setup|get|fetch) (?:your |the |my |an? )?(?:offline|local) (?:brain|ai|mode|model)", t):
        return "ai_setup", {}
    if F(r"(?:is )?(?:your |the )?(?:offline|local) (?:brain|ai)(?: status| ready| done)?\??|(?:how(?:'s| is) )?(?:the )?(?:offline|local) (?:brain|ai) (?:download )?(?:going|coming along)", t):
        return "ai_status", {}
    if F(r"(?:forget|clear|reset) (?:our|this|the) (?:conversation|chat)|(?:let'?s )?start (?:a )?(?:new conversation|over)|new (?:conversation|chat)", t):
        return "ai_forget", {}
    if F(r"(?:link|connect) (?:yourself |jarvis )?(?:to|with) tab|how do i (?:link|connect) (?:you )?(?:to|with) tab", t):
        return "tab_link_help", {}
    m = F(r"(?:switch|change|go|cut|flip)(?: over)? (?:the )?(?:obs )?(?:scene )?to (?:the |my )?(.+?) scene", t) or \
        F(r"(?:switch|change|set) (?:the )?(?:obs )?scene to (?:the |my )?(.+)", t) or F(r"(?:obs )?scene (.+)", t)
    if m:
        return "obs_scene", {"name": m.group(1)}
    return None

# Phrases that need Tab (the internet, his accounts, other people) rather than the offline brain
TAB_TASK = re.compile(r"\btab\b|\be-?mails?\b|\binbox\b|\bcalendar\b|\btext (?!me\b)\w+|\bsend (?:a |an )?(?:message|text)|"
                      r"\border (?:me |a |some )|\bbuy\b|\bbook (?:a |me )|\breservation\b|\blook (?:it |that |this )?up\b|"
                      r"\bsearch (?:for|the web|online)\b|\bgoogle\b|\bnews\b|\blatest\b|\bscores?\b|\bprice of\b|\bstock\b|"
                      r"\btoday'?s\b|\bright now\b|\bon my phone\b", re.I)

def route(text, linked, ai_ready):
    """Where a phrase the built-in commands don't know goes: 'tab', 'ai' or None (nothing can answer it).
    The offline brain answers chat and questions; Tab, when linked, takes tasks that need the internet or his accounts."""
    if linked and (TAB_TASK.search(text or "") or not ai_ready):
        return "tab"
    if ai_ready:
        return "ai"
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
        self.forward = forward               # callable(text) -> sends to Tab; None = not linked
        from .localai import LocalAI
        self.ai = LocalAI(self.cfg)          # the offline brain (Ollama); answers what the commands don't
        self.makers = makers.Makers(self)    # code + image creation, local and safety-checked
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
        if k in ("open_clips", "clips_folder"):
            return self.clips(k == "clips_folder")
        if k in NOT_ON_LINUX:
            log.info("%s asked for; Windows-only", k)
            return self.say(f"{NOT_ON_LINUX[k]} isn't available on Linux yet, {s}. Say \u201cswitch to Windows\u201d if you need it now.")
        return self.say(f"That one isn't available on Linux yet, {s}.")

    def clips(self, folder_only=False):
        """ "Open my last clip" / "open my clips folder": OBS's recording folder first, then Videos/Captures etc."""
        from .core import wsdata
        s = self.sir
        folders = []
        try:
            if self.studio.obs_running():
                d = self.studio.record_dir()
                if d:
                    folders.append(d)
        except Exception as e:
            log.info("OBS record dir: %r", e)
        folders += [f for f in wsdata.clip_folders() if f not in folders]
        if folder_only:
            if not folders:
                return self.say(f"I can't find a clips folder yet, {s}. OBS saves to the folder set in its Settings, Output.")
            self.launch(["xdg-open", folders[0]])
            return self.say(f"Your clips folder, {s}.")
        clips = wsdata.recent_clips(folders, limit=1)
        if not clips:
            return self.say(f"I can't find any clips yet, {s}. Say \u201cclip that\u201d while OBS is open.")
        self.launch(["xdg-open", clips[0][0]])
        return self.say(f"Your latest clip, {s}.")

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

    def is_gaming(self):
        gw = getattr(self, "gamewatch", None)
        try:
            return bool(gw and gw.gaming)
        except Exception:
            return False

    # ------------------------------------------------------------------
    def handle(self, text):
        low = commands.normalize(text)
        hit = parse_os(low)
        if hit:
            return self.os_action(*hit)
        it = qol.parse(low)
        if it is not None:
            return self.qol.handle(it, self.ask)
        return self.fallback(text)

    def fallback(self, raw):
        """Anything the built-in commands don't know: the offline brain, or Tab when linked (see route())."""
        s = self.sir
        try:
            ready = self.ai.ready()
        except Exception:
            ready = False
        where = route(raw, bool(self.forward), ready)
        if where == "ai":
            stream = getattr(self.ai, "ask_stream", None)
            if stream is not None:              # the race: sentences are spoken as they arrive
                res = stream(raw, self.say, sir=s, in_game=self.is_gaming())
                if res is not None and res.ok:
                    return res.text
            else:
                ans = self.ai.ask(raw, sir=s)
                if ans:
                    return self.say(ans)
            where = "tab" if self.forward else None
        if where == "tab":
            self.forward(raw)
            return self.say(f"Passing that to Tab, {s}.")
        from . import localai
        st = localai.read_state().get("status")
        if st == "installing":
            return self.say(localai.status_line(sir=s) + " Until then I can do my built-in commands.")
        if self.ai.enabled:
            return self.say(f"I can't answer that one yet, {s}. Say \u201cdownload your offline brain\u201d and I'll learn to chat "
                            f"without the internet, or \u201cwhat can you do\u201d for my built-in commands.")
        return self.say(f"I can't do that one offline, {s}. Say \u201cwhat can you do\u201d for my local commands.")

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
        if action == "ai_setup":
            from . import localai
            if self.ai.ready():
                missing = []
                if hasattr(self.ai, "available") and hasattr(self.ai, "racers"):
                    have = set(self.ai.available())
                    missing = [m for m in self.ai.racers() if m not in have]
                if missing and isinstance(self.run, Runner):
                    import threading
                    st = localai.read_state()
                    threading.Thread(target=localai.pull_extras, args=(self.cfg, self.ai.model(), localai.notifier(), st),
                                     name="ai-extras", daemon=True).start()
                    return self.say(f"My offline brain is here, {s}. I'm adding {len(missing)} more models in the background "
                                    f"so they can race to answer you.")
                return self.say(f"My offline brain is already here, {s}: {self.ai.model()}.")
            if localai.read_state().get("status") == "installing" and getattr(self, "_ai_thread", None) and self._ai_thread.is_alive():
                return self.say(localai.status_line(sir=s))
            if not self.ai.enabled:
                return self.say(f"My offline brain is switched off in my settings, {s}.")
            import threading
            self._ai_thread = threading.Thread(target=localai.setup, args=(self.cfg,), kwargs={"say": self.say},
                                               name="ai-setup", daemon=True)
            if isinstance(self.run, Runner):
                self._ai_thread.start()
            return self.say(f"Downloading my offline brain, {s}. It's a few gigabytes, so give me a few minutes; "
                            f"I'll tell you when it's ready.")
        if action == "ai_status":
            from . import localai
            if self.ai.ready():
                ms = self.ai.available() if hasattr(self.ai, "available") else []
                if len(ms) > 1:
                    return self.say(f"My offline brain is ready, {s}: {len(ms)} models race to answer, the first good answer wins.")
                return self.say(f"My offline brain is ready, {s}: {self.ai.model()}.")
            return self.say(localai.status_line(sir=s))
        if action == "ai_forget":
            self.ai.forget()
            return self.say(f"Fresh start, {s}.")
        if action == "tab_link_help":
            if self.forward:
                return self.say(f"I'm already linked to Tab, {s}.")
            return self.say(f"I work without Tab, {s}, but linking adds the bigger jobs. Text Tab \u201clink code for JARVIS OS\u201d, "
                            f"then open the JARVIS menu in the tray and choose Link to Tab.")
        if action == "whats_new":
            from . import whatsnew
            try:
                whatsnew.open_card(launch=None if isinstance(self.run, Runner) else self.run)
            except Exception as e:
                log.info("what's new card: %r", e)
            return self.say(whatsnew.SPOKEN)
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
    return self.fallback(it.raw or str(it.arg))

def _handle(self, text):
    low = commands.normalize(text)
    hit = parse_os(low)
    if hit:
        return self.os_action(*hit)
    ws = getattr(self, "workshop", None)
    if ws is None:                          # holograms, vitals, the update countdown, the 3D brain (Windows v5.6 parity)
        from .workshop import Workshop
        ws = self.workshop = Workshop(self)
    said = ws.edit(text)                    # while a hologram is open: "make it purple and gold", "spin faster" ...
    if said is not None:
        return said
    hit = workshop.parse(text)
    if hit is not None:
        return ws.handle(*hit)
    mk = makers.parse(text)                 # "write code for ...", "make an image of ..." (local, $0, safety-checked)
    if mk is not None:
        return self.makers.handle(*mk)
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
