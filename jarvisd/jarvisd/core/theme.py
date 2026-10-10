"""v4 look: accent colour themes and the bundled fonts (Orbitron + Rajdhani, both SIL OFL)."""
from __future__ import annotations
import logging, os
from PySide6 import QtGui

log = logging.getLogger("jarvis.theme")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Six full palettes (v4). Each: main glow, light, deep, accent, background dark, background lift, glow strength, label.
THEMES = {
    "cyan":       dict(main=(92, 225, 255), light=(190, 245, 255), deep=(20, 110, 160), accent=(255, 170, 40),
                       bg0=(4, 10, 20), bg1=(9, 24, 40), glow=1.0, label="Arc Cyan"),
    "mark3":      dict(main=(255, 70, 58), light=(255, 214, 140), deep=(130, 20, 22), accent=(255, 196, 64),
                       bg0=(14, 5, 7), bg1=(36, 12, 14), glow=1.0, label="Mark III (red and gold)"),
    "stealth":    dict(main=(205, 210, 218), light=(255, 255, 255), deep=(62, 66, 72), accent=(150, 156, 166),
                       bg0=(5, 5, 6), bg1=(17, 17, 19), glow=0.35, label="Stealth (black and white)"),
    "vibranium":  dict(main=(176, 96, 255), light=(236, 214, 255), deep=(84, 28, 156), accent=(255, 92, 214),
                       bg0=(9, 4, 19), bg1=(26, 11, 47), glow=1.2, core2=(255, 110, 225), label="Vibranium (purple)"),
    "hulkbuster": dict(main=(255, 142, 30), light=(255, 216, 150), deep=(130, 58, 8), accent=(255, 210, 70),
                       bg0=(13, 8, 4), bg1=(34, 20, 8), glow=1.0, label="Hulkbuster (orange and amber)"),
    "ultron":     dict(main=(232, 18, 44), light=(255, 112, 122), deep=(105, 0, 14), accent=(196, 200, 210),
                       bg0=(2, 1, 2), bg1=(13, 5, 7), glow=1.15, label="Ultron (crimson on black)"),
}
ORDER = list(THEMES)
ALIASES = {"arc cyan": "cyan", "arc": "cyan", "arc reactor": "cyan", "blue": "cyan", "default": "cyan", "classic": "cyan",
           "normal": "cyan", "jarvis": "cyan", "teal": "cyan", "light blue": "cyan",
           "mark 3": "mark3", "mark iii": "mark3", "mark three": "mark3", "mark3": "mark3", "mark": "mark3", "red and gold": "mark3",
           "red": "mark3", "gold": "mark3", "iron man": "mark3", "ironman": "mark3",
           "black and white": "stealth", "black": "stealth", "white": "stealth", "grey": "stealth", "gray": "stealth",
           "silver": "stealth", "minimal": "stealth", "dark": "stealth",
           "black panther": "vibranium", "panther": "vibranium", "wakanda": "vibranium", "purple": "vibranium",
           "violet": "vibranium", "pink": "vibranium",
           "hulk buster": "hulkbuster", "hulk": "hulkbuster", "orange": "hulkbuster", "amber": "hulkbuster", "yellow": "hulkbuster",
           "crimson": "ultron", "evil": "ultron", "dark red": "ultron", "blood red": "ultron"}

_current = "cyan"
_listeners = []

def resolve(name):
    n = " ".join((name or "").strip().lower().replace(" theme", "").split())
    if n in THEMES:
        return n
    return ALIASES.get(n)

def next_name(cur=None):
    cur = cur or _current
    return ORDER[(ORDER.index(cur) + 1) % len(ORDER)] if cur in ORDER else ORDER[0]

def current():
    return _current

def set_theme(name):
    global _current
    n = resolve(name) or "cyan"
    if n != _current:
        _current = n
        for fn in list(_listeners):
            try:
                fn(n)
            except Exception:
                log.exception("theme listener failed")
    return n

def on_change(fn):
    _listeners.append(fn)

def _q(rgb, a=255):
    return QtGui.QColor(rgb[0], rgb[1], rgb[2], a)

def _p(key):
    return THEMES[_current][key]

def main(a=255):   return _q(_p("main"), a)
def light(a=255):  return _q(_p("light"), a)
def deep(a=255):   return _q(_p("deep"), a)
def accent(a=255): return _q(_p("accent"), a)
def glow():        return float(_p("glow"))
def core2(a=255):
    c = THEMES[_current].get("core2"); return _q(c, a) if c else None
def label(name=None): return THEMES[resolve(name) or _current]["label"]

def hexa(col):
    return "#%02x%02x%02x" % (col.red(), col.green(), col.blue())

def rgba(col, a):
    return "rgba(%d,%d,%d,%d)" % (col.red(), col.green(), col.blue(), a)

def bg0(): return _q(_p("bg0"))
def bg1(): return _q(_p("bg1"))

# ---------- fonts ----------
DISPLAY = "Segoe UI"   # replaced by Orbitron once loaded
BODY = "Segoe UI"      # replaced by Rajdhani once loaded
_loaded = False

def load_fonts():
    """Register the bundled fonts with Qt (needs a QGuiApplication). Safe to call twice."""
    global DISPLAY, BODY, _loaded
    if _loaded:
        return DISPLAY, BODY
    d = os.path.join(ROOT, "assets", "fonts")
    fams = {}
    for f in ("Orbitron-Bold.ttf", "Orbitron-Medium.ttf", "Rajdhani-Medium.ttf", "Rajdhani-SemiBold.ttf"):
        p = os.path.join(d, f)
        if not os.path.exists(p):
            continue
        fid = QtGui.QFontDatabase.addApplicationFont(p)
        if fid >= 0:
            for fam in QtGui.QFontDatabase.applicationFontFamilies(fid):
                fams[f.split("-")[0]] = fam
        else:
            log.warning("font %s could not be loaded", f)
    DISPLAY = fams.get("Orbitron", DISPLAY)
    BODY = fams.get("Rajdhani", BODY)
    _loaded = True
    log.info("fonts: display=%s body=%s", DISPLAY, BODY)
    return DISPLAY, BODY

def font(kind="body", px=14, bold=False, spacing=0.0):
    f = QtGui.QFont(DISPLAY if kind == "display" else BODY)
    f.setPixelSize(max(6, int(px)))
    f.setWeight(QtGui.QFont.Bold if bold else (QtGui.QFont.Medium if kind == "display" else QtGui.QFont.DemiBold if kind == "body" else QtGui.QFont.Normal))
    if spacing:
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, spacing)
    f.setHintingPreference(QtGui.QFont.PreferNoHinting)
    return f
