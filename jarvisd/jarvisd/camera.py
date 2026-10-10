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

def has_device(dev_root="/dev", sys_root="/sys/class/video4linux"):
    """A real webcam is plugged in: a /dev/video* node whose device isn't virtual. v4l2loopback (the OBS virtual
    camera, loaded on JARVIS OS) creates /dev/video0 under /sys/devices/virtual, and OpenCV can't capture from it."""
    try:
        nodes = [n for n in os.listdir(dev_root) if n.startswith("video")]
    except OSError:
        return False
    if not os.path.isdir(sys_root):
        return bool(nodes)
    for n in nodes:
        real = os.path.realpath(os.path.join(sys_root, n))
        if os.path.exists(real) and "/devices/virtual/" not in real:
            return True
    return False

def quiet_watcher_cls(dev_root="/dev", sys_root="/sys/class/video4linux"):
    """The v5.1 FaceWatcher, but it only asks OpenCV to open a webcam when /dev has a video device. Without one,
    OpenCV's V4L2 backend logs two warnings on every 3-minute re-check (seen in boot-test run 38070835970)."""
    from .core.camera import FaceWatcher
    class QuietFaceWatcher(FaceWatcher):
        def _open(self):
            if not has_device(dev_root, sys_root):
                return None
            return super()._open()
    return QuietFaceWatcher

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
        watcher_cls = quiet_watcher_cls()
    w = watcher_cls(ccfg, brain.state_dir, lambda: speak(greeting(brain.sir, ccfg, getattr(brain, "weather_line", None))),
                    is_gaming=is_gaming)
    w.start()
    return w
