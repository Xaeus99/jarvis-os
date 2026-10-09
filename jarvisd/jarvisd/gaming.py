"""JARVIS OS gaming + streaming tuning: "game mode", "stream mode", "gaming check", "set up my monitor".

Bazzite already ships the heavy lifting (GameMode, gamescope, MangoHud, OBS vkcapture hooks, scx schedulers,
tuned power profiles, zstd zram, vm.max_map_count, split-lock mitigation off). This module only switches
those on and off at the right moment and reports what the hardware is doing. Everything is reversible.
"""
from __future__ import annotations
import json, os, re

STATE = os.path.join(os.path.expanduser("~"), ".local", "state", "jarvis", "mode.json")

def _state(path=None):
    try:
        return json.load(open(path or STATE))
    except (OSError, ValueError):
        return {"mode": "normal"}

def _save(st, path=None):
    path = path or STATE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(st, open(path, "w"))

def mode_steps(kind, on, cfg=None):
    """The exact commands for a mode change, in order. kind: 'game' | 'stream'."""
    cfg = cfg or {}
    steps = []
    if on:
        steps.append(["powerprofilesctl", "set", "performance"])            # tuned-ppd -> throughput-performance-bazzite
        if cfg.get("game_scheduler"):                                       # opt-in: sched_ext via scx_loader (needs admin)
            steps.append(["pkexec", "scxctl", "start", "--sched", cfg["game_scheduler"], "--mode", "gaming"])
        if kind == "stream":
            steps.append(["pw-metadata", "-n", "settings", "0", "clock.force-quantum", str(cfg.get("stream_quantum", 256))])
            steps.append(["balooctl6", "suspend"])                          # file indexer off while live
            steps.append(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                          "--object-path", "/org/freedesktop/Notifications", "--method",
                          "org.freedesktop.Notifications.Inhibit", "jarvis", "Streaming", "{}"])
    else:
        steps.append(["powerprofilesctl", "set", cfg.get("normal_profile", "balanced")])
        if cfg.get("game_scheduler"):
            steps.append(["pkexec", "scxctl", "stop"])
        steps.append(["pw-metadata", "-n", "settings", "0", "clock.force-quantum", "0"])
        steps.append(["balooctl6", "resume"])
    return steps

def set_mode(run, kind, on, cfg=None, state_path=None):
    """Runs the steps; returns (ok_count, failed_steps). Remembers the notification-inhibit cookie to release it."""
    st = _state(state_path)
    failed, ok = [], 0
    for argv in mode_steps(kind, on, cfg):
        rc, out = run(argv, timeout=20)
        if rc == 0:
            ok += 1
            m = re.search(r"uint32 (\d+)", out or "")
            if m:
                st["inhibit_cookie"] = int(m.group(1))
        else:
            failed.append(argv[0] if argv[0] != "pkexec" else argv[1])
    if not on and st.get("inhibit_cookie"):
        run(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications", "--object-path",
             "/org/freedesktop/Notifications", "--method", "org.freedesktop.Notifications.UnInhibit",
             str(st.pop("inhibit_cookie"))], timeout=10)
    st["mode"] = kind if on else "normal"
    _save(st, state_path)
    return ok, failed

# ---------- display: native resolution, highest refresh, VRR ----------

def best_display_commands(kscreen_json, hdr=None):
    """From `kscreen-doctor -j`, the commands that put every connected output at its native resolution and highest
    refresh rate with adaptive sync on 'automatic'. HDR only when asked: hdr=True enables, False disables, None leaves it."""
    cfg = json.loads(kscreen_json) if isinstance(kscreen_json, str) else kscreen_json
    cmds = []
    for o in cfg.get("outputs", []):
        if not o.get("connected") or not o.get("enabled", True):
            continue
        modes = o.get("modes") or []
        if not modes:
            continue
        def area(m):
            s = m.get("size") or {}
            return (s.get("width", 0) * s.get("height", 0))
        native = max(area(m) for m in modes)
        best = max((m for m in modes if area(m) == native), key=lambda m: float(m.get("refreshRate", 0)))
        name = o["name"]
        if str(o.get("currentModeId")) != str(best.get("id")):
            cmds.append(f"output.{name}.mode.{best['id']}")
        cmds.append(f"output.{name}.vrrpolicy.automatic")
        if hdr is not None:
            onoff = "enable" if hdr else "disable"
            cmds += [f"output.{name}.hdr.{onoff}", f"output.{name}.wcg.{onoff}"]
    return cmds

# ---------- gaming check ----------

def parse_bar1(nvidia_smi_q):
    """ReBAR is on when BAR1 is (about) as large as the VRAM. Returns (bar1_mib, vram_mib) or None."""
    def grab(section):
        m = re.search(section + r"\s*\n\s*Total\s*:\s*(\d+)\s*MiB", nvidia_smi_q)
        return int(m.group(1)) if m else None
    bar1, fb = grab(r"BAR1 Memory Usage"), grab(r"FB Memory Usage")
    return (bar1, fb) if bar1 and fb else None

def gaming_check(run):
    """Facts, not guesses: each line says what was read and from where."""
    lines = []
    rc, out = run(["nvidia-smi", "--query-gpu=name,driver_version,temperature.gpu", "--format=csv,noheader"], timeout=15)
    lines.append(("GPU", out.strip().replace(", ", " · ") if rc == 0 and out.strip() else "NVIDIA driver not answering"))
    rc, out = run(["nvidia-smi", "-q", "-d", "MEMORY"], timeout=15)
    bar = parse_bar1(out) if rc == 0 else None
    if bar:
        lines.append(("Resizable BAR", ("on" if bar[0] >= bar[1] * 0.9 else "off (turn on Re-Size BAR + Above 4G in BIOS)")
                      + f" · BAR1 {bar[0]} MiB / VRAM {bar[1]} MiB"))
    rc, out = run(["powerprofilesctl", "get"], timeout=10)
    lines.append(("Power profile", out.strip() if rc == 0 else "unknown"))
    rc, out = run(["gamemoded", "--status"], timeout=10)
    lines.append(("GameMode", out.strip() if rc == 0 else "not running (add gamemoderun %command% in Steam)"))
    rc, out = run(["scxctl", "get"], timeout=10)
    lines.append(("CPU scheduler", out.strip() if rc == 0 and out.strip() else "kernel default (EEVDF)"))
    rc, out = run(["kscreen-doctor", "-j"], timeout=10)
    if rc == 0:
        try:
            for o in json.loads(out).get("outputs", []):
                if o.get("connected") and o.get("enabled", True):
                    cur = next((m for m in o.get("modes", []) if str(m.get("id")) == str(o.get("currentModeId"))), {})
                    vrr = {0: "never", 1: "always", 2: "automatic"}.get(o.get("vrrPolicy"), o.get("vrrPolicy"))
                    lines.append((f"Display {o['name']}", f"{cur.get('name', '?')} · VRR {vrr}"))
        except ValueError:
            pass
    return lines
