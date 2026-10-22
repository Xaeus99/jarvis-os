"""JARVIS OS "what's new": a card shown once on the first login after each JARVIS OS update (by the HUD process,
jarvis-hud.service, which starts with the desktop), and again whenever he says "Jarvis, what's new?".
The card itself is the Windows v5 card (core/whatsnew.py) with the JARVIS OS list.
"""
from __future__ import annotations
import logging, os, subprocess, sys

log = logging.getLogger("jarvisd.whatsnew")
VERSION_FILE = "/usr/lib/jarvis-os/version"
SEEN = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "jarvis", "whatsnew_seen")

LINES = [
    ("JARVIS is the operating system now", "Same voice, same Tab link, on Linux. Say \u201cswitch to Windows\u201d whenever you need it."),
    ("Workspace: Ctrl+Shift+J", "Clock, weather, PC stats, music, clips and history. Or say \u201copen the workspace\u201d."),
    ("Streaming & games", "\u201cGo live\u201d \u00b7 \u201cClip that\u201d \u00b7 switch scenes. The HUD stays out of your games and game mode turns itself on."),
    ("Updates you can undo", "Signed updates install on their own; \u201croll back the update\u201d undoes the last one."),
    ("Graphics check", "I check the NVIDIA driver at every login and tell you how to fix it. Ask: \u201ccheck my graphics card\u201d."),
    ("Safe by default", "Restarting, switching to Windows and Tab taking control always ask first. Meta+Shift+Esc takes it back."),
]
SPOKEN = ("I'm the operating system now, sir. Control shift J opens the workspace; say go live, clip that, or switch scenes "
          "for OBS; the HUD stays out of your games; and every update can be rolled back.")

def os_version(path=VERSION_FILE):
    try:
        for line in open(path):
            if line.startswith("JARVIS_OS_VERSION="):
                return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        pass
    return None

def should_show(version, seen_path=SEEN):
    if not version:
        return False
    try:
        return open(seen_path).read().strip() != version
    except OSError:
        return True

def mark_seen(version, seen_path=SEEN):
    os.makedirs(os.path.dirname(seen_path), exist_ok=True)
    with open(seen_path, "w") as f:
        f.write(version + "\n")

def make_card(version=None, seconds=60):
    from PySide6 import QtGui
    from .core import theme as T
    from .core.whatsnew import WhatsNewCard
    from .hud import load_fonts
    load_fonts(QtGui, T)
    card = WhatsNewCard(version or "", seconds=seconds)
    card.lines = LINES
    card.title = "JARVIS OS" + (f"  \u00b7  {version}" if version else "")
    card.subtitle = "Welcome to your new system, sir. Here's what I can do on it."
    card.place()
    return card

SETUP_SUBTITLE = "Welcome to JARVIS OS, sir. Here's where setup stands."

def make_setup_card(version=None, its=None, seconds=90):
    """The very first login: the setup check (setupcheck) in the what's-new card's layout."""
    from . import setupcheck
    card = make_card(version, seconds=seconds)
    card.lines = setupcheck.card_lines(its if its is not None else setupcheck.items())
    card.title = "JARVIS OS setup"        # the version is in the footer: the title line has room for ~20 characters
    card.subtitle = SETUP_SUBTITLE
    card.kicker = "FIRST START"
    card.footer = "Ask \u201chow's setup going\u201d any time." + (f"  \u00b7  {version.split('.20')[0]}" if version else "")
    return card

def maybe_show(QtCore, delay_ms=2500, version=None, seen_path=SEEN):
    """Called by the HUD process at login. Returns the card (keep a reference) or None.
    First login ever (nothing seen yet): the setup card. First login after an update: what's new."""
    version = version or os_version()
    if not should_show(version, seen_path):
        return None
    first = not os.path.exists(seen_path)
    try:
        card = make_setup_card(version) if first else make_card(version)
    except Exception as e:
        log.info("what's new card unavailable: %r", e)
        return None
    QtCore.QTimer.singleShot(delay_ms, card.show)
    mark_seen(version, seen_path)
    return card

def open_card(launch=None):
    """For "Jarvis, what's new?": the card in its own short-lived process."""
    argv = [sys.executable, "-m", "jarvisd.whatsnew"]
    if launch:
        return launch(argv)
    env = dict(os.environ); env.setdefault("QT_QPA_PLATFORM", "xcb")
    subprocess.Popen(argv, env=env, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return 0, ""

def main(argv=None):
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])
    card = make_card(os_version())
    card.show()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
