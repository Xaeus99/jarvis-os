"""JARVIS OS first-boot setup check: one plain list of where each piece of the new system stands.

Shown on the very first login (the setup card, in place of "what's new"), asked by voice ("how's setup going",
"is everything set up") and printed by `jarvisctl setup status [--json]`. Every line is read, not assumed:
  Graphics      the graphics check (gpucheck): NVIDIA driver on the RTX card, Secure Boot key, safe graphics
  Apps          the first-boot Flathub install (jarvis-first-boot-apps.service, ~/.local/state/jarvis/apps.done)
  Offline brain the local AI download (localai.json)
  Microphone    whether PipeWire knows a real microphone
  Windows       whether the firmware boot menu has Windows Boot Manager ("switch to Windows")
  Tab link      whether JARVIS is linked to Tab (~/.config/jarvis/config.json)
States: ok (done), busy (still going on its own), todo (needs him, with the words to say), warn (something's wrong).
"""
from __future__ import annotations
import json, os, subprocess

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.join(HOME, ".local", "state")), "jarvis")
CONFIG = os.path.join(HOME, ".config", "jarvis", "config.json")
MARK = {"ok": "\u2713", "busy": "\u2026", "todo": "\u2192", "warn": "!"}

def _run(run, argv, timeout=8):
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return 127, ""

def graphics_item(gpu):
    if gpu is None:
        return ("Graphics", "warn", "The graphics check couldn't run.", "Say \u201ccheck my graphics card\u201d.")
    if gpu["code"] == "no_nvidia":
        return ("Graphics", "ok", "No NVIDIA card here (a virtual machine or another GPU).", "")
    state = "ok" if gpu["code"] == "ok" else ("todo" if gpu["code"] in ("safe_graphics", "secure_boot_key") else "warn")
    return ("Graphics", state, gpu["headline"], gpu.get("fix", ""))

def apps_item(run, state_dir=STATE_DIR):
    if os.path.exists(os.path.join(state_dir, "apps.done")):
        return ("Apps", "ok", "Your apps are installed from Flathub.", "")
    rc, out = _run(run, ["systemctl", "--user", "is-active", "jarvis-first-boot-apps.service"])
    st = out.strip()
    if st in ("active", "activating"):
        return ("Apps", "busy", "Installing your apps from Flathub in the background.", "")
    if st == "failed":
        return ("Apps", "warn", "Some apps didn't install (usually no internet yet).", "Say \u201cinstall my apps\u201d.")
    return ("Apps", "todo", "Your apps aren't installed yet.", "Say \u201cinstall my apps\u201d once you're online.")

def brain_item(state_dir=STATE_DIR):
    try:
        st = json.load(open(os.path.join(state_dir, "localai.json")))
    except (OSError, ValueError):
        st = {}
    s = st.get("status")
    if s == "ready":
        return ("Offline brain", "ok", "Downloaded; I can answer without the internet.", "")
    if s == "installing":
        return ("Offline brain", "busy", f"Downloading, step {st.get('step', 1)} of 2, {st.get('progress', 0)}%.", "")
    if s == "off":
        return ("Offline brain", "ok", "Switched off in settings.", "")
    if s == "failed":
        return ("Offline brain", "warn", f"The download stopped: {st.get('error') or 'unknown error'}.", "Say \u201cdownload your offline brain\u201d.")
    return ("Offline brain", "todo", "Not downloaded yet.", "Say \u201cdownload your offline brain\u201d.")

def mic_item(run):
    rc, out = _run(run, ["pactl", "list", "short", "sources"], 5)
    if rc != 0:
        return ("Microphone", "busy", "The sound system is still starting.", "")
    real = [l for l in out.splitlines() if len(l.split("\t")) > 1 and not l.split("\t")[1].strip().endswith(".monitor")]
    if real:
        return ("Microphone", "ok", "Found; say \u201cHey JARVIS\u201d or press Ctrl+Shift+Space.", "")
    return ("Microphone", "todo", "No microphone found.", "Plug in your headset or webcam mic; I'll pick it up on my own.")

def windows_item(run):
    rc, out = _run(run, ["efibootmgr"], 6)
    if rc != 0:
        return ("Windows", "warn", "I can't read the boot menu.", "")
    if "windows boot manager" in out.lower():
        return ("Windows", "ok", "Windows is untouched; say \u201cswitch to Windows\u201d any time.", "")
    return ("Windows", "warn", "I don't see Windows Boot Manager in the boot menu.", "Pick Windows from your PC's boot menu (F11/F12) and tell Tab.")

def tab_item(config=CONFIG):
    try:
        d = json.load(open(config))
    except (OSError, ValueError):
        d = {}
    if (d.get("forwarding") or {}).get("to") and (d.get("replies") or {}).get("key"):
        return ("Tab link", "ok", "Linked; say \u201cask Tab to ...\u201d.", "")
    return ("Tab link", "todo", "Not linked yet.", "Say \u201clink to Tab\u201d, or run jarvisctl link --code with the code Tab texts you.")

def items(run=subprocess.run, gpu="check", state_dir=STATE_DIR, config=CONFIG):
    if gpu == "check":
        try:
            from . import gpucheck
            gpu = gpucheck.check(run=run)
        except Exception:
            gpu = None
    return [graphics_item(gpu), apps_item(run, state_dir), brain_item(state_dir), mic_item(run), windows_item(run), tab_item(config)]

def summary(its):
    left = [i for i in its if i[1] in ("todo", "warn")]
    busy = [i for i in its if i[1] == "busy"]
    if not left and not busy:
        return "Everything's set up."
    parts = []
    if busy:
        parts.append("still going on its own: " + ", ".join(i[0].lower() for i in busy))
    if left:
        parts.append("needs you: " + ", ".join(i[0].lower() for i in left))
    return "; ".join(parts).capitalize() + "."

def spoken(its, sir="sir"):
    s = f"Setup check, {sir}. " + summary(its)
    for name, state, words, fix in its:
        if state in ("todo", "warn"):
            s += f" {name}: {words}" + (f" {fix}" if fix else "")
    return s

def card_lines(its):
    """(title, line) pairs for the setup card (the what's-new card's layout)."""
    return [(f"{MARK[state]}  {name}", words + (f" {fix}" if fix and state != "ok" else "")) for name, state, words, fix in its]

def text(its):
    out = [summary(its)]
    for name, state, words, fix in its:
        out.append(f"{MARK[state]} {name:<14} {words}" + (f"\n  {'':<14} {fix}" if fix else ""))
    return "\n".join(out)
