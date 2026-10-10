"""JARVIS OS stand-in for the Windows audioout.py: the bits the workspace's output picker uses (PipeWire via pactl)."""
from __future__ import annotations
import subprocess

def is_default(name):
    return (name or "").strip().lower() in ("", "default", "system default", "@default_audio_sink@")

def devices(run=None):
    """Output devices' descriptions (sinks), for the workspace's picker."""
    try:
        out = run(["pactl", "list", "sinks"])[1] if run else subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [l.split(":", 1)[1].strip() for l in (out or "").splitlines() if l.strip().startswith("Description:")]

def sink_for(description, run=None):
    try:
        out = run(["pactl", "list", "sinks"])[1] if run else subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    name = None
    for l in (out or "").splitlines():
        l = l.strip()
        if l.startswith("Name:"):
            name = l.split(":", 1)[1].strip()
        elif l.startswith("Description:") and l.split(":", 1)[1].strip() == description:
            return name
    return None
