"""Camera greeting on JARVIS OS: "Welcome back, sir. Good evening." when he sits down after being away, plus
"learn my face" / "forget my face" / "turn the camera greeting on/off". The engine is the Windows one
(core/camera.py, OpenCV on V4L2); frames stay in memory, only the optional face set is saved, on this PC.

Paused while a game runs (gamewatch) and while an app that needs the webcam is open (OBS, Discord, video calls):
on Linux only one program can stream from a webcam at a time.
"""
from __future__ import annotations
import datetime, logging, os

log = logging.getLogger("jarvisd.camera")

def available():
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False

def has_device(dev_root="/dev"):
    try:
        return any(n.startswith("video") for n in os.listdir(dev_root))
    except OSError:
        return False

def greeting(sir="sir", ccfg=None, weather_line=None, now=None):
    ccfg = ccfg or {}
    now = now or datetime.datetime.now()
    part = "morning" if now.hour < 12 else "afternoon" if now.hour < 17 else "evening"
    line = f"Welcome back, {sir}. Good {part}."
    if ccfg.get("greet_with_time", True):
        line += f" It's {now.strftime('%I:%M %p').lstrip('0')}."
    if ccfg.get("greet_with_weather", True) and weather_line:
        try:
            w = weather_line()
        except Exception:
            w = None
        if w and "couldn't" not in w:
            line += " " + w
    return line

def start(cfg, brain, speak, is_gaming=lambda: False, watcher_cls=None):
    """Start the camera greeting thread. Returns the watcher, or None (no OpenCV / switched off in the config)."""
    ccfg = cfg.get("camera", {}) or {}
    if ccfg.get("disabled") or not available():
        if not available():
            log.info("camera greeting off: OpenCV isn't installed")
        return None
    if watcher_cls is None:
        from .core.camera import FaceWatcher as watcher_cls
    w = watcher_cls(ccfg, brain.state_dir, lambda: speak(greeting(brain.sir, ccfg, getattr(brain, "weather_line", None))),
                    is_gaming=is_gaming)
    w.start()
    return w
