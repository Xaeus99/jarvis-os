"""Is a game running? (JARVIS OS version of the Windows gamewatch.py.)

Windows looked at the foreground window (fullscreen / exe under steamapps). Wayland doesn't let one program see
another's window, so JARVIS OS looks at what is observable instead:
  1. Steam games: every process Steam launches carries SteamAppId / SteamGameId (or STEAM_COMPAT_APP_ID for Proton)
     in its environment (/proc/<pid>/environ, readable for your own processes), and Steam's launch wrapper runs as
     `reaper SteamLaunch AppId=<id> -- ...`.
  2. gamescope (a game started inside the gamescope compositor).
  3. Feral GameMode active (`gamemoded -s`): Bazzite's games request it, Lutris/Heroic too.
  4. Extra process names from the config (gamewatch.game_processes), minus gamewatch.never_hide_for.

What JARVIS does with it (same as Windows, plus JARVIS OS's game mode):
  - the HUD ring stays hidden in games except while JARVIS listens/answers (hud.json "game");
  - optional auto game mode (gamewatch.auto_game_mode, default on): performance power profile while a game runs,
    back to normal afterwards, only if JARVIS switched it on itself.
"""
from __future__ import annotations
import logging, os, re, threading, time

log = logging.getLogger("jarvisd.gamewatch")

NOT_GAMES = {"steam", "steamwebhelper", "steam-runtime-l", "srt-bwrap", "pressure-vessel", "pv-adverb", "pv-bwrap",
             "steamerrorreporter", "fossilize_replay", "gameoverlayui", "steam-launch-wrapper", "obs", "discord",
             "wallpaperengine", "linux-wallpaperengine"}
NOT_GAME_APPIDS = {"0", "7", "769", "228980", "1070560", "1391110", "1493710", "1628350", "2180100", "431960"}
# Steam client, Steamworks redist, Steam Linux Runtime 1/2/3 (soldier, sniper), Proton EasyAntiCheat runtime,
# Proton Experimental, Wallpaper Engine (431960), Proton Hotfix: tools, not games
GAMESCOPE = {"gamescope", "gamescope-wl"}

def _read(path, n=262144):
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""

def scan_procs(proc_root="/proc"):
    """[(pid, name, cmdline, env_subset)] for this user's processes. Never raises."""
    out = []
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return out
    keys = (b"SteamAppId=", b"SteamGameId=", b"STEAM_COMPAT_APP_ID=")
    for pid in pids:
        base = os.path.join(proc_root, pid)
        name = _read(os.path.join(base, "comm"), 64).decode("utf-8", "replace").strip()
        if not name:
            continue
        cmd = _read(os.path.join(base, "cmdline"), 4096).replace(b"\0", b" ").decode("utf-8", "replace").strip()
        env = {}
        raw = _read(os.path.join(base, "environ"))
        if raw and any(k in raw for k in keys):
            for item in raw.split(b"\0"):
                for k in keys:
                    if item.startswith(k):
                        env[k[:-1].decode()] = item[len(k):].decode("utf-8", "replace")
        out.append((int(pid), name, cmd, env))
    return out

def classify(procs, gamemode=False, cfg=None):
    """Pure decision. Returns {"gaming": bool, "why": str, "appid": str|None, "pid": int|None}."""
    cfg = cfg or {}
    never = {x.lower() for x in cfg.get("never_hide_for", [])}
    extra = {x.lower() for x in cfg.get("game_processes", [])}
    for pid, name, cmd, env in procs:
        n = (name or "").lower()
        if n in never:
            continue
        if n == "reaper" and "SteamLaunch" in cmd:
            m = re.search(r"AppId=(\d+)", cmd)
            if m and m.group(1) not in NOT_GAME_APPIDS:
                return {"gaming": True, "why": "steam", "appid": m.group(1), "pid": pid}
        if n not in NOT_GAMES and n != "reaper":
            for k in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
                v = (env or {}).get(k, "").strip()
                if v and v.isdigit() and v not in NOT_GAME_APPIDS:
                    return {"gaming": True, "why": "steam", "appid": v, "pid": pid}
        if n in extra:
            return {"gaming": True, "why": "process", "appid": None, "pid": pid}
        if n in GAMESCOPE and cfg.get("use_gamescope", True):
            return {"gaming": True, "why": "gamescope", "appid": None, "pid": pid}
    if gamemode and cfg.get("use_gamemode", True):
        return {"gaming": True, "why": "gamemode", "appid": None, "pid": None}
    return {"gaming": False, "why": "", "appid": None, "pid": None}

def gamemode_active(run):
    """Feral GameMode's own status line: 'gamemode is active' / 'gamemode is inactive'. False if it isn't installed."""
    if run is None:
        return False
    rc, out = run(["gamemoded", "-s"], timeout=3)
    return rc == 0 and "is active" in (out or "")

def detect(cfg=None, run=None, proc_root="/proc"):
    try:
        return classify(scan_procs(proc_root), gamemode_active(run) if (cfg or {}).get("use_gamemode", True) else False, cfg)
    except Exception as e:                # never take the daemon down over a /proc race
        log.info("game detection failed: %r", e)
        return {"gaming": False, "why": "", "appid": None, "pid": None}

class AutoGameMode:
    """Switches JARVIS OS game mode on when a game starts and off when it ends, but only if JARVIS turned it on:
    a game mode or stream mode the person chose stays as it is."""
    def __init__(self, set_mode, current_mode):
        self.set_mode, self.current_mode = set_mode, current_mode
        self.ours = False

    def __call__(self, gaming):
        mode = self.current_mode()
        if gaming and mode == "normal":
            self.set_mode("game", True); self.ours = True
        elif not gaming and self.ours:
            if mode == "game":
                self.set_mode("game", False)
            self.ours = False

class GameWatcher:
    """Polls detect() every `interval` s on a thread; calls each listener(gaming: bool, info) when it changes."""
    def __init__(self, detect_fn, interval=3.0, listeners=()):
        self.detect_fn, self.interval = detect_fn, interval
        self.listeners = list(listeners)
        self.gaming = False; self.info = {}
        self._stop = threading.Event(); self.thread = None

    def poll(self):
        info = self.detect_fn() or {}
        g = bool(info.get("gaming"))
        changed = g != self.gaming
        self.gaming, self.info = g, info
        if changed:
            log.info("game %s (%s %s)", "started" if g else "ended", info.get("why"), info.get("appid") or "")
            for fn in self.listeners:
                try:
                    fn(g, info)
                except Exception as e:
                    log.info("game listener failed: %r", e)
        return changed

    def start(self):
        def loop():
            while not self._stop.is_set():
                self.poll()
                self._stop.wait(self.interval)
        self.thread = threading.Thread(target=loop, name="gamewatch", daemon=True); self.thread.start()
        return self

    def stop(self):
        self._stop.set()

def wire(cfg, run, hud=None, mode_state=None):
    """Build the watcher the daemon runs: HUD hide-in-games + auto game mode. Returns the (unstarted) watcher."""
    from . import gaming
    gcfg = cfg.get("gamewatch", {}) or {}
    watcher = GameWatcher(lambda: detect(gcfg, run), float(gcfg.get("interval_seconds", 3)))
    if hud is not None:
        watcher.listeners.append(lambda g, info: hud.publish(game=g))
    if gcfg.get("auto_game_mode", True):
        auto = AutoGameMode(lambda kind, on: gaming.set_mode(run, kind, on, cfg.get("gaming", {}), mode_state),
                            lambda: gaming._state(mode_state).get("mode", "normal"))
        watcher.listeners.append(lambda g, info: auto(g))
    return watcher
