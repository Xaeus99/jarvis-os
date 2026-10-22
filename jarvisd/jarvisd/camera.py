"""Webcam features on JARVIS OS: removed (1.0.0-rc2, Oct 22 2026, at the owner's request).

The "welcome back" camera greeting, "learn my face" / "forget my face" and the camera greeting on/off commands are
gone, and any face data an earlier version saved (~/.local/state/jarvis/faces) is deleted when JARVIS starts.
Nothing in JARVIS OS opens the webcam. (On Windows, Face Lab is the only feature that uses it.)
Old phrases still get an answer instead of silence: "That's been removed, sir."
"""
from __future__ import annotations
import logging, os, shutil

log = logging.getLogger("jarvisd.camera")
REMOVED_KINDS = ("learn_face", "forget_face", "camera_on", "camera_off")

def removed_line(sir="sir"):
    return f"That's been removed, {sir}."

def purge_face_data(state_dir):
    """Delete the saved face set from earlier versions. True if something was deleted."""
    p = os.path.join(state_dir, "faces")
    if os.path.isdir(p) or os.path.islink(p):
        shutil.rmtree(p, ignore_errors=True) if not os.path.islink(p) else os.remove(p)
        log.info("face data deleted (%s)", p)
        return True
    return False
