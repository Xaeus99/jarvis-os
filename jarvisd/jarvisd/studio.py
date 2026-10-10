"""OBS on JARVIS OS: "go live", "stop the stream", "start/stop recording", "clip that", "switch to the <name> scene".

Same behaviour as Windows JARVIS (studio.py), through OBS's built-in websocket (obs-websocket v5, core/obs.py).
OBS here is the Flathub app com.obsproject.Studio. The websocket password is never in this code or the repo: it
comes from the person's own OBS settings (obs-websocket's config.json, which OBS itself writes) or from
~/.config/jarvis/config.json (stream.obs_websocket.password). If OBS's websocket server has never been switched on,
JARVIS switches it on (with a fresh random password, stored only in OBS's own settings) before OBS starts.
"""
from __future__ import annotations
import json, logging, os, re, secrets, subprocess, time

from .core.obs import OBS, OBSError

log = logging.getLogger("jarvisd.studio")
FLATPAK_ID = "com.obsproject.Studio"
APP_IDS = {"obs": FLATPAK_ID, "discord": "com.discordapp.Discord", "vesktop": "dev.vencord.Vesktop"}

def obs_config_dirs(home=None):
    home = home or os.path.expanduser("~")
    return [os.path.join(home, ".var", "app", FLATPAK_ID, "config", "obs-studio"),     # the Flatpak (JARVIS OS default)
            os.path.join(home, ".config", "obs-studio")]                               # a native OBS, if someone has one

def websocket_file(home=None):
    for d in obs_config_dirs(home):
        p = os.path.join(d, "plugin_config", "obs-websocket", "config.json")
        if os.path.exists(p):
            return p
    return None

def websocket_settings(cfg=None, home=None):
    """{host, port, password, enabled, source}. JARVIS's own config wins; otherwise OBS's own websocket settings."""
    o = ((cfg or {}).get("stream", {}) or {}).get("obs_websocket", {}) or {}
    out = {"host": o.get("host", "127.0.0.1"), "port": int(o.get("port", 4455)), "password": o.get("password", ""),
           "enabled": None, "source": "jarvis config" if o.get("password") else None}
    p = websocket_file(home)
    if p:
        try:
            w = json.load(open(p))
            out["enabled"] = bool(w.get("server_enabled"))
            if "port" not in o and w.get("server_port"):
                out["port"] = int(w["server_port"])
            if not out["password"] and w.get("auth_required", True) and w.get("server_password"):
                out["password"] = w["server_password"]; out["source"] = "OBS settings"
        except (OSError, ValueError) as e:
            log.info("obs-websocket config unreadable: %r", e)
    return out

def enable_websocket(home=None):
    """Switch obs-websocket on in OBS's own settings, keeping an existing password or making a random one.
    Only while OBS is closed (OBS rewrites the file on exit). Returns the file path."""
    p = websocket_file(home) or os.path.join(obs_config_dirs(home)[0], "plugin_config", "obs-websocket", "config.json")
    try:
        w = json.load(open(p))
    except (OSError, ValueError):
        w = {}
    w.update({"server_enabled": True, "auth_required": True, "alerts_enabled": False})
    w.setdefault("server_port", 4455)
    if not w.get("server_password"):
        w["server_password"] = secrets.token_urlsafe(18)
    w.setdefault("first_load", False)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(w, f, indent=4)
    os.chmod(tmp, 0o600); os.replace(tmp, p)
    return p

def obs_running(procs=None):
    """OBS (Flatpak or native) is running: a process named obs, or the Flatpak's bwrap/flatpak wrapper for it."""
    if procs is None:
        import psutil
        procs = []
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                procs.append((p.info.get("name") or "", " ".join(p.info.get("cmdline") or [])))
            except Exception:
                pass
    for name, cmd in procs:
        if name in ("obs", "obs-studio"):                 # the Flatpak's process is still called obs on the host
            return True
        if FLATPAK_ID in cmd and re.search(r"\bflatpak\b.*\brun\b", cmd):
            return True
    return False

def detached(argv):
    """Start an app without waiting for it (Runner would kill it at its timeout). Returns (code, text)."""
    try:
        subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return 0, ""
    except FileNotFoundError:
        return 127, f"{argv[0]}: not installed"
    except OSError as e:
        return 1, str(e)

def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower().replace("_", " ").replace("-", " ")).strip()

def match_scene(name, scenes):
    """Spoken scene name -> the closest real OBS scene name, or None."""
    want = _norm(re.sub(r"\bscene\b", "", name or ""))
    if not want:
        return None
    by = {_norm(s): s for s in scenes}
    if want in by:
        return by[want]
    for n, s in by.items():
        if want in n.split() or (len(want) >= 3 and want in n):
            return s
    try:
        from rapidfuzz import process, fuzz
        hit = process.extractOne(want, list(by), scorer=fuzz.WRatio)
        if hit and hit[1] >= 75:
            return by[hit[0]]
    except ImportError:
        import difflib
        hit = difflib.get_close_matches(want, list(by), n=1, cutoff=0.6)
        if hit:
            return by[hit[0]]
    return None

class Studio:
    def __init__(self, cfg=None, launch=detached, run=None, set_mode=None, clock=time.monotonic, sleep=time.sleep,
                 client_factory=None, procs=None, home=None):
        cfg = cfg or {}
        self.full = cfg
        self.cfg = cfg.get("stream", {}) or {}
        self.ccfg = cfg.get("clip", {}) or {}
        self.launch, self.run, self.set_mode = launch, run, set_mode
        self.clock, self.sleep = clock, sleep
        self.client = None
        self.client_factory = client_factory or (lambda st: OBS(st["host"], st["port"], st["password"]).connect())
        self.procs = procs                      # tests: a callable returning [(name, cmdline)]
        self.home = home

    def obs_running(self):
        return obs_running(self.procs() if self.procs else None)

    def _client(self, wait=0.0):
        end = self.clock() + wait
        while True:
            st = websocket_settings(self.full, self.home)
            try:
                if self.client is None:
                    self.client = self.client_factory(st)
                self.client.request("GetVersion")
                return self.client, None
            except Exception as e:
                if self.client:
                    try: self.client.close()
                    except Exception: pass
                self.client = None
                if isinstance(e, OBSError) and "password" in str(e):
                    return None, str(e)          # waiting won't fix a wrong password
                if self.clock() >= end:
                    return None, repr(e)
                self.sleep(1.5)

    def record_dir(self):
        c, _ = self._client()
        if not c:
            return None
        try:
            return (c.request("GetRecordDirectory") or {}).get("recordDirectory")
        except Exception:
            return None

    def warm(self):
        """At JARVIS start: if OBS is already open, switch on its replay buffer for "clip that"."""
        if not self.ccfg.get("replay_buffer", True) or not self.obs_running():
            return False
        c, err = self._client()
        if c:
            return c.ensure_replay_buffer(self.ccfg.get("seconds", 30))
        log.info("OBS open but its websocket isn't reachable: %s", err)
        return False

    def _open_apps(self):
        launched = []
        for key in self.cfg.get("launch", ["obs", "discord"]):
            app = APP_IDS.get(key.split("|")[0].strip().lower(), key)
            if app == FLATPAK_ID:
                if self.obs_running():
                    continue
                st = websocket_settings(self.full, self.home)
                if not st["enabled"] and not st["source"] == "jarvis config":
                    try:
                        log.info("switched on OBS's websocket server in %s", enable_websocket(self.home))
                    except OSError as e:
                        log.info("couldn't switch on OBS's websocket: %r", e)
            elif self.run and self.run(["pgrep", "-f", app], timeout=5)[0] == 0:
                continue
            code, _ = self.launch(["flatpak", "run", app])
            if code == 0:
                launched.append(key)
        return launched

    def start(self, mode="stream", sir="sir"):
        """mode: 'stream' (go live) or 'record'. Returns the sentence JARVIS says."""
        launched = self._open_apps()
        if mode == "stream" and self.set_mode:
            try: self.set_mode("stream", True)          # JARVIS OS stream tuning (gaming.py): performance, no indexer
            except Exception as e: log.info("stream tuning: %r", e)
        vol = self.cfg.get("volume")
        wait = float(self.cfg.get("obs_wait_seconds", 25)) if launched or self.obs_running() else 0
        c, err = self._client(wait=wait)
        if not c:
            log.info("OBS websocket unavailable: %s", err)
            what = "OBS is starting up" if "obs" in launched else "OBS is open"
            pw = "password" in (err or "")
            hint = "OBS rejected the websocket password" if pw else "I can't reach OBS's remote control"
            fix = ("Check Tools, WebSocket Server Settings in OBS; I read the password from there." if pw else
                   "Turn on Tools, WebSocket Server Settings, Enable WebSocket server in OBS, and I'll handle it next time.")
            return (f"{what}, {sir}, but {hint}, so you'll need to press Start "
                    f"{'Streaming' if mode == 'stream' else 'Recording'} yourself. {fix}")
        if self.ccfg.get("replay_buffer", True):
            c.ensure_replay_buffer(self.ccfg.get("seconds", 30))
        if mode == "record":
            if c.record_active():
                return f"Already recording, {sir}."
            c.request("StartRecord")
            return f"Recording, {sir}."
        if c.stream_active():
            return f"You're already live, {sir}."
        c.request("StartStream")
        self.sleep(2.5)
        if not c.stream_active():
            return f"I pressed Start Streaming, {sir}, but OBS isn't live yet. Check your stream key in OBS settings."
        return f"Stream mode engaged. You're live, {sir}. Good luck out there."

    def stop(self, mode="stream", sir="sir"):
        c, err = self._client()
        if not c:
            return f"I can't reach OBS's remote control, {sir}, so you'll need to stop it in OBS."
        if mode == "record":
            if not c.record_active():
                return f"Nothing's recording, {sir}."
            ok, d = c.ok("StopRecord")
            return f"Recording stopped and saved, {sir}." if ok else f"OBS wouldn't stop recording: {d}"
        if not c.stream_active():
            return f"You're not live right now, {sir}."
        ok, d = c.ok("StopStream")
        if ok and self.set_mode:
            try: self.set_mode("stream", False)
            except Exception as e: log.info("stream tuning off: %r", e)
        return f"Stream ended, {sir}. Nicely done." if ok else f"OBS wouldn't stop the stream: {d}"

    def clip(self, sir="sir"):
        """Save OBS's replay buffer. Linux has no Game Bar, so without OBS there's nothing to clip from."""
        if not self.obs_running():
            return (f"I can't clip without OBS open, {sir}; Linux has no Game Bar. "
                    f"Say \u201cstart recording\u201d or open OBS and I'll keep a replay buffer running.")
        c, err = self._client()
        if not c:
            return f"OBS is open, {sir}, but I can't reach its remote control, so I couldn't clip that."
        if c.replay_active():
            try:
                path = c.save_replay()
                return f"Clipped, {sir}." + (f" Saved as {os.path.basename(path)}." if path else "")
            except OBSError as e:
                log.info("save replay: %s", e)
                return f"OBS wouldn't save the clip, {sir}: {e}"
        if c.ensure_replay_buffer(self.ccfg.get("seconds", 30)):
            return (f"OBS's replay buffer wasn't running, {sir}, so that moment is gone. "
                    f"It's on now; the next clip will work.")
        return f"OBS's replay buffer won't switch on, {sir}. Check Settings, Output, Replay Buffer in OBS."

    def scene(self, name, sir="sir"):
        if not self.obs_running():
            return f"OBS isn't open, {sir}."
        c, err = self._client()
        if not c:
            return f"I can't reach OBS's remote control, {sir}."
        ok, d = c.ok("GetSceneList")
        scenes = [s.get("sceneName") for s in (d.get("scenes") or [])] if ok else []
        hit = match_scene(name, scenes)
        if not hit:
            names = ", ".join(scenes[:6]) or "none"
            return f"I don't see a scene called {name}, {sir}. Your scenes are: {names}."
        ok, d = c.ok("SetCurrentProgramScene", {"sceneName": hit})
        return f"Switched to {hit}, {sir}." if ok else f"OBS wouldn't switch scenes: {d}"
