"""rc9: colour "protocols" on JARVIS OS, a light port of Windows JARVIS 7.7's protocols.

"Jarvis, purple protocol" re-themes JARVIS (the HUD ring and the workspace) and, when the workspace is open, plays a short
effect inside the workspace window: a crackle, a blackout, the "PURPLE PROTOCOL" title, an orb burst, then the panels
light back up in the new colour. "Normal protocol" puts back the look from before the first protocol.
Nothing draws over the desktop or other apps (no full-screen overlay on KDE Wayland), and no effect plays during a game.
"<colour> theme" / "next theme" change the colour quietly, without the effect.
"""
from __future__ import annotations
import json, logging, os, re, time

log = logging.getLogger("jarvisd.protocols")
CFG_PATH = os.path.expanduser("~/.config/jarvis/config.json")
DEFAULT_THEME = "vibranium"          # the JARVIS OS look (workspace and HUD default)

# protocol word -> theme key (core/theme.py). Anything else core/theme.resolve() knows works too.
PROTOCOLS = {
    "purple": "vibranium", "violet": "vibranium", "pink": "vibranium", "magenta": "vibranium", "vibranium": "vibranium",
    "red": "mark3", "gold": "mark3", "red and gold": "mark3",
    "blue": "cyan", "cyan": "cyan", "arc": "cyan", "ice": "cyan", "teal": "cyan",
    "orange": "hulkbuster", "amber": "hulkbuster", "yellow": "hulkbuster", "sunset": "hulkbuster",
    "crimson": "ultron", "blood": "ultron", "blood red": "ultron", "dark red": "ultron", "danger": "ultron",
    "stealth": "stealth", "black": "stealth", "white": "stealth", "silver": "stealth", "ghost": "stealth",
    "shadow": "stealth", "night": "stealth", "dark": "stealth", "black and white": "stealth",
}
NORMAL_WORDS = {"normal", "default", "standard", "regular", "reset", "clean slate", "base", "home"}
_GO = r"(?:(?:activate|engage|initiate|start|run|begin|launch|enable|execute|commence|go|switch to|go to)(?: the)? )?"
_STOP_FIRST = {"what", "which", "how", "why", "is", "are", "the", "a", "an", "about", "tell", "explain", "my", "your",
               "internet", "http", "https", "tcp", "network", "security", "communication", "new", "this", "that"}
SAY_LIST = "purple, red, blue, orange, crimson or stealth"

def _clean(text):
    t = (text or "").lower().replace("\u2019", "'").strip()
    t = re.sub(r"[!?.,]+$", "", t)
    t = re.sub(r"^(?:(?:hey |ok |okay )?jarvis[, ]*)", "", t)
    t = re.sub(r"^(?:please |can you |could you |let's |lets |go ahead and |i want |initiate the |activate the )+", "", t).strip()
    return re.sub(r"(?:,? please|,? for me|,? jarvis|,? now)+$", "", t).strip()

def parse_voice(text):
    """('engage', word, theme) | ('normal', None, None) | ('unknown', word, None) | None (not a protocol phrase)."""
    t = _clean(text)
    if re.fullmatch(r"(?:end|stop|cancel|exit|disengage|deactivate|turn off|kill|drop|abort)(?: the)?(?: [a-z]+){0,2} protocols?"
                    r"|protocols? (?:off|over|end(?:ed)?|done|disengaged?)", t):
        return ("normal", None, None)
    m = re.fullmatch(_GO + r"([a-z]+(?: (?:and )?[a-z]+)?) protocol(?: (?:now|on|engaged?|activated?|go|initiated?))?", t) or \
        re.fullmatch(r"protocol ([a-z]+(?: [a-z]+)?)", t)
    if not m:
        return None
    word = m.group(1).strip()
    if word.split()[0] in _STOP_FIRST:
        return None
    if word in NORMAL_WORDS:
        return ("normal", None, None)
    theme = PROTOCOLS.get(word)
    if theme is None:
        from .core import theme as T
        theme = T.resolve(word) if word not in ("normal",) else None
    return ("engage", word, theme) if theme else ("unknown", word, None)

def parse_theme(low):
    """'<colour> theme', 'switch the theme to red', 'next theme' (the Windows grammar) -> theme key | 'next' | None."""
    from .core import commands
    t = _clean(low)
    if re.fullmatch(r"(?:(?:switch|change|set|go|turn)(?: (?:on|to|into))? )?(?:dark|light)(?: mode| theme)?(?: (?:on|off))?", t):
        return None                     # Windows/KDE dark or light mode is its own command (qol theme_mode)
    rx = next((r for r, tag in commands.V4_RULES if tag == "theme"), None)
    if not rx or not re.fullmatch(rx, t):
        return None
    if re.fullmatch(r"(?:next|change|switch|cycle)(?: the)? (?:theme|colou?rs?)", t):
        return "next"
    from .core import theme as T
    m = re.search(commands.THEME_WORDS, t)
    return T.resolve(m.group(0)) if m else None

def load_cfg(path=None):
    try:
        return json.load(open(path or CFG_PATH))
    except (OSError, ValueError):
        return {}

def save_cfg(update, path=None):
    path = path or CFG_PATH
    cfg = load_cfg(path)
    update(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, path)
    return cfg

def _show(theme, title, effect, send, hud):
    """Tell the running workspace (effect + recolour) and the HUD ring (recolour). Both are optional."""
    shown = None
    if send is not None:
        try:
            shown = send("workspace", {"cmd": "protocol", "title": title, "theme": theme, "effect": bool(effect)})
        except Exception as e:
            log.info("protocol: workspace unreachable: %r", e)
    if hud is not None:
        try:
            hud.publish(theme=theme, theme_ts=time.time())
        except Exception as e:
            log.info("protocol: HUD unreachable: %r", e)
    return shown

def engage(word, theme, sir="sir", path=None, send=None, hud=None, gaming=False):
    def upd(c):
        base = (c.get("protocol") or {}).get("base") or c.get("theme") or DEFAULT_THEME
        c["protocol"] = {"name": word, "theme": theme, "base": base, "since": int(time.time())}
        c["theme"] = theme
    save_cfg(upd, path)
    title = f"{word.upper()} PROTOCOL"
    shown = _show(theme, title, not gaming, send, hud)
    log.info("protocol: %s -> theme %s (workspace %s, gaming %s)", title, theme, "open" if shown else "not running", gaming)
    return f"{word.capitalize()} protocol engaged, {sir}."

def normal(sir="sir", path=None, send=None, hud=None, gaming=False):
    from .core import theme as T
    cfg = load_cfg(path)
    p = cfg.get("protocol")
    if not p:
        return f"No protocol is running, {sir}. This is the normal look."
    base = T.resolve(p.get("base")) or DEFAULT_THEME
    def upd(c):
        c.pop("protocol", None); c["theme"] = base
    save_cfg(upd, path)
    _show(base, "NORMAL PROTOCOL", not gaming, send, hud)
    log.info("protocol: normal -> theme %s", base)
    return f"Normal protocol, {sir}. Back to {T.label(base).split(' (')[0]}."

def set_theme(name, sir="sir", path=None, send=None, hud=None):
    """'<colour> theme' / 'next theme': recolour quietly (no effect); the new colour becomes the normal look."""
    from .core import theme as T
    cfg = load_cfg(path)
    cur = T.resolve(cfg.get("theme")) or DEFAULT_THEME
    n = T.next_name(cur) if name == "next" else (T.resolve(name) or cur)
    def upd(c):
        c.pop("protocol", None); c["theme"] = n
    save_cfg(upd, path)
    _show(n, "", False, send, hud)
    return f"{T.label(n)} theme, {sir}."

def unknown_words(word, sir="sir"):
    return f"I don't have a {word} protocol, {sir}. Try {SAY_LIST}, or normal protocol."
