"""v5.1: quality-of-life commands JARVIS handles on the PC by himself (no Tab round trip).

Two halves:
  parse(low, app_names) -> Intent("qol", action, extra={...}) or None   (pure Python, tested anywhere)
  QoL(brain).handle(intent, ask) -> spoken reply                         (calls Hands for anything Windows)

Every action is a concrete allowlisted thing. Nothing here deletes files, buys anything or messages anyone.
The few power actions (restart, sign out, hibernate, empty the Recycle Bin) always ask first, and never run
from a guessed / repaired phrase. Uncertain phrases ("set a timer" with no time) ask a question instead of
being forwarded to Tab.
"""
from __future__ import annotations
import ast, datetime, glob, json, logging, math, operator, os, random, re, socket, subprocess, threading, time

log = logging.getLogger("jarvis.qol")
IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000

# =====================================================================================
#  numbers, durations, clock times
# =====================================================================================
ONES = {"zero": 0, "oh": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
        "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "couple": 2, "few": 3}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}

def words_to_number(s):
    """'twenty five' -> 25, '25' -> 25, '2.5' -> 2.5, 'a hundred' -> 100. None if not a number."""
    s = (s or "").strip().lower().replace(",", "")
    if not s:
        return None
    try:
        v = float(s)
        return int(v) if v.is_integer() else v
    except ValueError:
        pass
    total, cur, seen = 0, 0, False
    for w in re.split(r"[\s-]+", s):
        if w in ("and", "of"):
            continue
        if w in ONES:
            cur += ONES[w]; seen = True
        elif w in TENS:
            cur += TENS[w]; seen = True
        elif w == "hundred":
            cur = (cur or 1) * 100; seen = True
        elif w == "thousand":
            total += (cur or 1) * 1000; cur = 0; seen = True
        elif w == "million":
            total += (cur or 1) * 1000000; cur = 0; seen = True
        else:
            try:
                cur += float(w) if "." in w else int(w); seen = True
            except ValueError:
                return None
    return total + cur if seen else None

NUM = (r"(?:\d+(?:\.\d+)?|(?:(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
       r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|couple(?: of)?|few)[\s-]?)+)")
UNIT_S = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
          "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}
DUR_RE = re.compile(r"(" + NUM + r")\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|h|m|s)\b(?:\s+and\s+a\s+half)?", re.I)

def parse_duration(s):
    """'5 minutes' / 'an hour and 30 minutes' / 'half an hour' / 'a minute and a half' / '90 seconds' -> seconds."""
    s = (s or "").lower().strip()
    if not s:
        return None
    s = s.replace("-", " ")
    total, hit = 0.0, False
    for pat, val in ((r"\bhalf (?:an|a) hour\b", 1800), (r"\b(?:a )?quarter (?:of )?(?:an|a) hour\b", 900)):
        if re.search(pat, s):
            total += val; hit = True; s = re.sub(pat, " ", s)
    for m in DUR_RE.finditer(s):
        n = words_to_number(m.group(1).strip())
        if n is None:
            continue
        unit = UNIT_S[m.group(2).lower()]
        total += n * unit
        if "and a half" in m.group(0):
            total += unit / 2
        hit = True
    if not hit:
        return None
    rest = DUR_RE.sub(" ", s)
    rest = re.sub(r"\b(?:and|for|in|a|an|of|the|timer|on)\b", " ", rest).strip()
    if rest:          # anything left that isn't glue: it wasn't (only) a duration
        return None
    return int(round(total)) if total > 0 else None

def say_duration(sec):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600); m, s = divmod(rem, 60)
    parts = []
    if h: parts.append(f"{h} hour" + ("s" if h != 1 else ""))
    if m: parts.append(f"{m} minute" + ("s" if m != 1 else ""))
    if s and not h: parts.append(f"{s} second" + ("s" if s != 1 else ""))
    if not parts: return "0 seconds"
    return " and ".join(parts)

def parse_clock(s, now=None):
    """'7:30 am' / '7 am' / 'seven thirty pm' / '19:00' / 'noon' / '6 tonight' / '7 tomorrow' -> next datetime."""
    now = now or datetime.datetime.now()
    s = (s or "").lower().strip().replace(".", "")
    tomorrow = bool(re.search(r"\btomorrow\b", s))
    s = re.sub(r"\b(?:tomorrow|today)\b", " ", s).strip()
    ampm = None
    if re.search(r"\b(?:a ?m|in the morning|this morning|morning)\b", s): ampm = "am"
    if re.search(r"\b(?:p ?m|in the (?:afternoon|evening)|tonight|at night|this (?:afternoon|evening)|evening|afternoon)\b", s): ampm = "pm"
    s = re.sub(r"\b(?:a ?m|p ?m|in the morning|in the afternoon|in the evening|this morning|this afternoon|this evening|tonight|"
               r"at night|morning|evening|afternoon|o'?clock|at)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s in ("noon", "midday"):
        h, mi, ampm = 12, 0, "24"
    elif s == "midnight":
        h, mi, ampm = 0, 0, "24"
    else:
        m = re.fullmatch(r"(\d{1,2})(?::|\s)?(\d{2})?", s)
        if m:
            h = int(m.group(1)); mi = int(m.group(2) or 0)
        else:
            parts = s.split()
            if not parts or parts[0] not in ONES and parts[0] not in TENS: return None
            h = words_to_number(parts[0])
            if h is None or not isinstance(h, int): return None
            mi = 0
            if len(parts) > 1:
                rest = " ".join(parts[1:])
                if rest in ("thirty", "half"): mi = 30
                elif rest in ("fifteen", "quarter"): mi = 15
                else:
                    rest = re.sub(r"^oh ", "", rest)
                    mi = words_to_number(rest)
                    if mi is None or not isinstance(mi, int): return None
    if not (0 <= h <= 23 and 0 <= mi <= 59): return None
    if ampm in ("am", "pm") and h > 12: ampm = None
    if ampm == "pm" and h < 12: h += 12
    if ampm == "am" and h == 12: h = 0
    cand = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if tomorrow:
        return cand + datetime.timedelta(days=1)
    if cand <= now:
        if ampm is None and 1 <= h < 12:          # "alarm at 7" said at 3 pm -> 7 pm today
            pm = cand + datetime.timedelta(hours=12)
            if pm > now: return pm
        cand += datetime.timedelta(days=1)
    return cand

def say_clock(dt, now=None):
    now = now or datetime.datetime.now()
    t = dt.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")
    if dt.date() == now.date(): return t
    if dt.date() == (now + datetime.timedelta(days=1)).date(): return t + " tomorrow"
    return t + dt.strftime(" on %A")

# =====================================================================================
#  safe arithmetic ("what's 15 percent of 80", "12 times 7", "square root of 144")
# =====================================================================================
MATH_WORDS = [
    (r"\bmultiplied by\b|\btimes\b|\bx\b|×|\*", "*"), (r"\bdivided by\b|\bover\b|÷|/", "/"), (r"\bplus\b|\+", "+"),
    (r"\bminus\b|−", "-"), (r"\bto the power of\b|\^", "^"), (r"\bmod(?:ulo)?\b", "%"),
]
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}

def _eval(node):
    if isinstance(node, ast.Expression): return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool): return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6): raise ValueError("too big")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS: return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt" and len(node.args) == 1:
        return math.sqrt(_eval(node.args[0]))
    raise ValueError("not arithmetic")

def calculate(expr):
    """Spoken arithmetic -> number, or None if it isn't plain arithmetic (so it goes to Tab instead)."""
    e = expr.lower().strip().rstrip("?").replace(",", "")
    e = re.sub(r"^(?:what(?:'s| is)|whats|calculate|compute|how much is|work out)\s+", "", e).strip()
    m = re.fullmatch(r"(.+?)\s*(?:percent|%) of (.+)", e)
    if m:
        a, b = words_to_number(m.group(1)), words_to_number(m.group(2))
        if a is None or b is None: return None
        return a / 100 * b
    e = re.sub(r"\b(?:the )?square root of (" + NUM + r")", lambda m: f" sqrt ( {m.group(1)} ) ", e)
    e = re.sub(r"(" + NUM + r") squared\b", lambda m: f" ( {m.group(1)} ) ^ 2 ", e)
    e = re.sub(r"(" + NUM + r") cubed\b", lambda m: f" ( {m.group(1)} ) ^ 3 ", e)
    for pat, op in MATH_WORDS:
        e = re.sub(pat, f" {op} ", e)
    tokens = re.split(r"\s*([-+*/%^()]|sqrt)\s*", e)
    out = []
    for tok in tokens:
        tok = tok.strip()
        if not tok: continue
        if tok in ("+", "-", "*", "/", "%", "(", ")", "sqrt"): out.append(tok); continue
        if tok == "^": out.append("**"); continue
        n = words_to_number(tok)
        if n is None: return None
        out.append(repr(n))
    e = " ".join(out)
    if not re.search(r"[-+*/%]|sqrt", e):
        return None
    try:
        v = _eval(ast.parse(e, mode="eval"))
    except Exception:
        return None
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)): return None
    return v

def say_number(v):
    if isinstance(v, float):
        if v.is_integer(): return f"{int(v):,}"
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return f"{v:,}"

# =====================================================================================
#  allowlists
# =====================================================================================
SITES = {   # spoken name -> URL (config "quick.sites" adds more; https only)
    "youtube": "https://www.youtube.com/", "gmail|my email|email|my inbox|inbox|my gmail": "https://mail.google.com/",
    "google drive|drive|my drive": "https://drive.google.com/", "google docs|docs": "https://docs.google.com/",
    "google calendar|calendar|my calendar": "https://calendar.google.com/", "google maps|maps": "https://maps.google.com/",
    "netflix": "https://www.netflix.com/", "twitch": "https://www.twitch.tv/", "github": "https://github.com/",
    "reddit": "https://www.reddit.com/", "chatgpt|chat gpt": "https://chatgpt.com/", "twitter|x": "https://x.com/",
    "instagram": "https://www.instagram.com/", "tiktok|tik tok": "https://www.tiktok.com/", "amazon": "https://www.amazon.com/",
    "wikipedia": "https://www.wikipedia.org/", "roblox website": "https://www.roblox.com/home",
    "terraria wiki": "https://terraria.wiki.gg/", "youtube studio": "https://studio.youtube.com/",
    "twitch dashboard|stream manager": "https://dashboard.twitch.tv/", "speed test|speedtest": "https://www.speedtest.net/",
}
SYSTEM = {  # spoken name -> (what to open, spoken label). Built-in Windows tools and ms-settings: pages only.
    "settings|windows settings|pc settings": ("ms-settings:", "Settings"),
    "task manager": ("taskmgr.exe", "Task Manager"),
    "calculator|calc": ("calc.exe", "the calculator"),
    "notepad": ("notepad.exe", "Notepad"),
    "file explorer|explorer|files|my files|this pc|my computer": ("explorer.exe", "File Explorer"),
    "control panel": ("control.exe", "the Control Panel"),
    "snipping tool": ("snippingtool.exe", "the Snipping Tool"),
    "paint|ms paint": ("mspaint.exe", "Paint"),
    "device manager": ("devmgmt.msc", "Device Manager"),
    "command prompt|cmd|terminal": ("wt.exe|cmd.exe", "a terminal"),
    "sound settings|audio settings": ("ms-settings:sound", "sound settings"),
    "display settings|monitor settings|screen settings": ("ms-settings:display", "display settings"),
    "bluetooth settings|bluetooth": ("ms-settings:bluetooth", "Bluetooth settings"),
    "wifi settings|wi-fi settings|wifi|network settings|internet settings": ("ms-settings:network", "network settings"),
    "windows update|update settings|updates": ("ms-settings:windowsupdate", "Windows Update"),
    "apps settings|installed apps|apps and features": ("ms-settings:appsfeatures", "installed apps"),
    "storage settings|storage": ("ms-settings:storagesense", "storage settings"),
    "mouse settings": ("ms-settings:mousetouchpad", "mouse settings"),
    "night light settings|night light": ("ms-settings:nightlight", "night light settings"),
    "power settings|power options": ("ms-settings:powersleep", "power settings"),
    "startup apps|startup settings": ("ms-settings:startupapps", "startup apps"),
    "game mode|game mode settings|gaming settings": ("ms-settings:gaming-gamemode", "Game Mode settings"),
    "game bar settings|capture settings|captures settings": ("ms-settings:gaming-gamedvr", "capture settings"),
    "notification settings|notifications settings": ("ms-settings:notifications", "notification settings"),
    "wallpaper settings|background settings|personalization": ("ms-settings:personalization-background", "background settings"),
    "recycle bin|trash": ("shell:RecycleBinFolder", "the Recycle Bin"),
}
FOLDERS = {"downloads": "Downloads", "download": "Downloads", "documents": "Documents", "docs": "Documents",
           "desktop": "Desktop", "pictures": "Pictures", "photos": "Pictures", "videos": "Videos", "music": "Music",
           "screenshots": "Screenshots", "captures": "Captures", "home": "Home", "user": "Home",
           "jarvis data": "JARVIS data", "appdata": "AppData"}
RISKY_EXT = {".exe", ".msi", ".msix", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".scr",
             ".lnk", ".jar", ".reg", ".hta", ".cpl", ".appx", ".appxbundle", ".pif", ".url", ".iso", ".img"}
PARTIAL_EXT = {".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload"}
HOLIDAYS = {"christmas|christmas day": (12, 25), "christmas eve": (12, 24), "halloween": (10, 31),
            "new year|new years|new year's|new year's day|new years day": (1, 1),
            "valentine's day|valentines day|valentine's|valentines": (2, 14),
            "the fourth of july|fourth of july|july 4th|july fourth|independence day": (7, 4),
            "new year's eve|new years eve": (12, 31), "st patrick's day|saint patrick's day": (3, 17)}

def _alias_match(name, table):
    n = re.sub(r"^(?:the|my|up)\s+", "", (name or "").lower()).strip(" .?")
    n = re.sub(r"\s+(?:website|site|web site|page|dot com|app)$", "", n).strip()
    for k, v in table.items():
        if n in [a.strip() for a in k.split("|")]:
            return k, v
    return None, None

# =====================================================================================
#  parser
# =====================================================================================
DESTRUCTIVE = {"restart_pc", "sign_out", "hibernate", "empty_bin"}
CONFIRM_TEXT = {"restart_pc": "Restart the PC, {s}? Unsaved work in open apps could be lost.",
                "sign_out": "Sign you out of Windows, {s}? Open apps will close.",
                "hibernate": "Hibernate the PC, {s}?",
                "empty_bin": "Permanently empty the Recycle Bin, {s}? That can't be undone."}

def Q(action, raw="", **extra):
    from .commands import Intent
    it = Intent("qol", action, raw=raw, extra=dict(extra))
    if action in DESTRUCTIVE:
        it.extra["destructive"] = True
    return it

TIMER_WORD = r"(?:timer|countdown|count down)"
KEYMAP = [(r"paste(?: (?:it|that|this|here))?", "paste"), (r"copy(?: (?:it|that|this))?", "copy"), (r"cut (?:it|that|this)", "cut"),
          (r"undo(?: (?:it|that|this))?", "undo"), (r"redo(?: (?:it|that|this))?", "redo"),
          (r"select (?:all|everything)", "select_all"), (r"save (?:it|this|that|the file|my work|this file)", "save"),
          (r"(?:new tab|open a new tab|open new tab)", "new_tab"), (r"close (?:this |the )?tab", "close_tab"),
          (r"(?:reopen|restore) (?:the )?(?:last |closed )?tab|bring back (?:the|that) tab", "reopen_tab"),
          (r"(?:refresh|reload)(?: (?:the|this) page| it| this)?", "refresh"),
          (r"(?:open (?:the )?)?emoji(?: panel| picker| keyboard)", "emoji"),
          (r"zoom in", "zoom_in"), (r"zoom out", "zoom_out"), (r"(?:reset (?:the )?zoom|zoom reset|normal zoom)", "zoom_reset")]
KEYSPEC = {"paste": "ctrl+v", "copy": "ctrl+c", "cut": "ctrl+x", "undo": "ctrl+z", "redo": "ctrl+y", "select_all": "ctrl+a",
           "save": "ctrl+s", "new_tab": "ctrl+t", "close_tab": "ctrl+w", "reopen_tab": "ctrl+shift+t", "refresh": "f5",
           "emoji": "win+.", "zoom_in": "ctrl+=", "zoom_out": "ctrl+-", "zoom_reset": "ctrl+0"}
KEYSAID = {"paste": "Pasted", "copy": "Copied", "cut": "Cut", "undo": "Undone", "redo": "Redone", "select_all": "Selected all",
           "save": "Saved", "new_tab": "New tab", "close_tab": "Tab closed", "reopen_tab": "Tab restored", "refresh": "Refreshed",
           "emoji": "Emoji panel", "zoom_in": "Zoomed in", "zoom_out": "Zoomed out", "zoom_reset": "Zoom reset"}

def parse(low, app_names=()):
    """low: normalized lower-case text. Returns a qol Intent, or None (= not ours, normal parsing continues)."""
    t = (low or "").strip(" .!?,")
    if not t:
        return None
    def mk(a, **x):
        return Q(a, raw=t, **x)
    F = re.fullmatch

    # ---------------- timers ----------------
    m = F(r"(?:set|start|make|create|put on|give me)(?: me)? (?:a |an )?(.+?) " + TIMER_WORD + r"(?: for (.+))?", t)
    if m:
        d = parse_duration(m.group(1)); label = None
        if not d and m.group(2):       # "set a pizza timer for 12 minutes"
            d = parse_duration(m.group(2)); label = m.group(1)
            if label in ("a", "an", "the", "my", "new", "quick"):
                label = None
        if d: return mk("timer_set", seconds=d, label=label)
    m = F(r"(?:set|start|make|create|put on|give me)(?: me)? (?:a |an |the )?" + TIMER_WORD + r"(?: for| of)? (.+?)(?: (?:called|named|labeled|labelled) (.+))?", t)
    if m and parse_duration(m.group(1)):
        return mk("timer_set", seconds=parse_duration(m.group(1)), label=m.group(2))
    m = F(TIMER_WORD + r"(?: for)? (.+)", t) or F(r"(.+?) " + TIMER_WORD, t) or F(r"count ?down (?:from )?(.+)", t)
    if m and parse_duration(m.group(1)):
        return mk("timer_set", seconds=parse_duration(m.group(1)), label=None)
    if F(r"(?:set|start|make|create)(?: me)? (?:a |an |the )?" + TIMER_WORD + r"|" + TIMER_WORD, t):
        return mk("timer_set", seconds=None, label=None)          # asks "For how long?"
    if F(r"how (?:much time|long)(?: is| do i have| have i got)?(?: left| remaining)?(?: on)? (?:the |my )?(?:timers?|countdown)|"
         r"(?:check|what'?s|how'?s) (?:the |my )?(?:timers?|countdown)(?: at| status| left)?|timer status|time left(?: on (?:the |my )?timer)?|"
         r"how much time is left|how much time left|what timers? (?:do i have|are (?:running|set|on))|(?:list|show)(?: me)? (?:my |the |all )?timers", t):
        return mk("timer_status")
    if F(r"(?:cancel|stop|delete|remove|clear|kill|turn off|end|reset)(?: all)?(?: of)? (?:the |my |all )?(?:my |the )?(?:timers?|countdowns?)", t):
        return mk("timer_cancel", all=bool(re.search(r"\ball\b|timers\b", t)))

    # ---------------- stopwatch ----------------
    if F(r"(?:start|begin|set|run)(?: a| the| my)? stop ?watch", t):
        return mk("stopwatch_start")
    if F(r"(?:stop|end|pause|finish)(?: the| my)? stop ?watch", t):
        return mk("stopwatch_stop")
    if F(r"(?:check|read)(?: the| my)? stop ?watch|how long (?:has|is) (?:the |my )?stop ?watch(?: been)?(?: going| running| at)?|stop ?watch(?: time| status)?", t):
        return mk("stopwatch_check")

    # ---------------- alarms ----------------
    m = F(r"(?:set|make|create|put)(?: me)? (?:an |a |my )?alarm (?:for |at |to )?(.+)|wake me(?: up)? (?:at|by|around) (.+)|alarm (?:for|at) (.+)", t)
    if m:
        when = next(g for g in m.groups() if g)
        dt = parse_clock(when)
        if dt is not None:
            return mk("alarm_set", at=dt.timestamp(), spoken=when)
        d = parse_duration(re.sub(r"^in ", "", when)) if when.startswith("in ") else None
        if d:
            return mk("alarm_set", at=None, after=d, spoken=when)
        return mk("alarm_set", at=None, heard=when)
    if F(r"(?:set|make|create)(?: me)? (?:an |a )?alarm|wake me up(?: later| tomorrow)?", t):
        return mk("alarm_set", at=None)
    if F(r"(?:what|which) alarms? (?:do i have|are set|have i set)|(?:list|show|check)(?: me)? (?:my |the |all )?alarms?|when is my alarm|"
         r"what time is my alarm(?: set for)?|do i have (?:an |any )?alarms?(?: set)?|(?:what|which) reminders? (?:do i have|are set)|(?:list|show|check)(?: me)? (?:my |the |all )?reminders", t):
        return mk("alarm_list")
    if F(r"(?:cancel|delete|remove|clear|turn off|disable)(?: all)?(?: of)? (?:my |the |all )?(?:my |the )?alarms?", t):
        return mk("alarm_cancel")
    if F(r"(?:cancel|delete|remove|clear)(?: all)?(?: of)? (?:my |the |all )?(?:my |the )?reminders?", t):
        return mk("remind_cancel")
    if F(r"(?:stop|silence|dismiss|shut off|turn off)(?: the| that)? (?:alarm|ringing|beeping)(?: sound)?|i'?m (?:up|awake)|stop ringing", t):
        return mk("ring_stop")
    m = F(r"snooze(?: (?:it|the alarm|alarm|the timer))?(?: for (.+))?", t)
    if m:
        return mk("snooze", seconds=parse_duration(m.group(1)) if m.group(1) else None)

    # ---------------- reminders ----------------
    for pat, kind, wi, xi in ((r"remind me (?:in|after) (.+?) (?:to|that|about) (.+)", "in", 1, 2),
                              (r"remind me (?:to|that|about) (.+) (?:in|after) (.+)", "in", 2, 1),
                              (r"remind me (?:at|by|around) (.+?) (?:to|that|about) (.+)", "at", 1, 2),
                              (r"remind me (?:to|that|about) (.+) (?:at|by|around) (.+)", "at", 2, 1)):
        m = F(pat, t)
        if not m:
            continue
        when, what = m.group(wi), m.group(xi)
        if kind == "in":
            d = parse_duration(when)
            if d: return mk("remind", at=None, after=d, what=what, spoken="in " + say_duration(d))
        else:
            dt = parse_clock(when)
            if dt: return mk("remind", at=dt.timestamp(), what=what, spoken="at " + say_clock(dt))
    m = F(r"remind me (?:to|that|about) (.+)", t)
    if m:
        return mk("remind", at=None, what=m.group(1))

    # ---------------- volume (exact, Core Audio) ----------------
    if F(r"what(?:'s| is) (?:the |my )?(?:volume|volume level)(?: at| set to)?|how loud is (?:it|the volume|my volume)|volume level|check (?:the )?volume", t):
        return mk("volume_get")
    if F(r"un ?mute(?: the| my)?(?: volume| sound| audio| pc| computer| it| speakers?)?|turn (?:the )?sound (?:back )?on|sound on", t):
        return mk("mute", on=False)
    if F(r"mute(?: the| my)?(?: volume| sound| audio| pc| computer| it| speakers?| everything)?|silence(?: the)? (?:pc|computer|sound|audio|everything)|turn (?:the )?sound off|sound off", t):
        return mk("mute", on=True)
    m = F(r"(?:set |turn |put |change )?(?:the )?([a-z][a-z0-9 ]{1,24}?)(?:'s)? volume (?:to |at )?(" + NUM + r")(?: percent| %)?", t)
    if m and _known_app_word(m.group(1), app_names) and _number_ok(m.group(2)):
        return mk("app_volume", app=_known_app_word(m.group(1), app_names), level=int(words_to_number(m.group(2))))
    m = F(r"(?:mute|silence) ([a-z][a-z0-9 ]{1,24})", t)
    if m and _known_app_word(m.group(1), app_names):
        return mk("app_mute", app=_known_app_word(m.group(1), app_names), on=True)
    m = F(r"un ?mute ([a-z][a-z0-9 ]{1,24})", t)
    if m and _known_app_word(m.group(1), app_names):
        return mk("app_mute", app=_known_app_word(m.group(1), app_names), on=False)

    # ---------------- media extras ----------------
    if F(r"what(?:'s| is) (?:playing|this song|the song|this track|the track|on spotify)|what song is (?:this|playing|on)|(?:which|what) song is this|who (?:sings|is singing) this(?: song)?|name (?:this|that) (?:song|tune)", t):
        return mk("now_playing")

    # ---------------- screenshots ----------------
    if F(r"(?:take|grab|capture|save|get|make)(?: me)? (?:a |an )?(?:full )?(?:screen ?shot|screen ?cap|screen capture|picture of (?:my |the )?screen)(?: of (?:my |the )?(?:whole )?screens?)?|"
         r"screen ?shot(?: (?:this|that|it|now))?|capture (?:my |the )?(?:whole )?screen|print screen", t):
        return mk("screenshot")
    if F(r"snip(?: it| this| that)?|(?:take|start|do) a snip|(?:take a |grab a )?(?:partial|region|area) screen ?shot|screen ?shot (?:a |the )?(?:region|part|area|bit)(?: of (?:the|my) screen)?|"
         r"(?:take|grab) a screen ?shot of (?:a|part of the|part of my) (?:region|area|part|screen)", t):
        return mk("snip")
    if F(r"(?:open|show|show me|pull up|bring up|go to)(?: my| the)? (?:screen ?shots?|screen ?shots? folder)|where are my screen ?shots", t):
        return mk("open_folder", folder="Screenshots")

    # ---------------- files / folders ----------------
    m = F(r"(?:open|show|show me|pull up|bring up|go to)(?: up)?(?: my| the)? (downloads?|documents|docs|desktop folder|pictures|photos|videos|music|captures|home|user|jarvis data|appdata)(?: folder| directory)?", t)
    if m:
        return mk("open_folder", folder=FOLDERS[m.group(1).replace(" folder", "")])
    if F(r"(?:open|show|show me|pull up)(?: my| the)? (?:jarvis|jarvis's) (?:folder|data folder|data|install folder|files)", t):
        return mk("open_folder", folder="JARVIS data" if "data" in t else "JARVIS")
    if F(r"(?:open|show|show me|pull up|run)(?: up)? (?:my |the )?(?:latest|last|newest|most recent|recent) (?:download|downloaded file|file i downloaded|thing i downloaded)|"
         r"open (?:what|the thing|the file) i (?:just )?downloaded|(?:open|show me) (?:the )?last thing i downloaded", t):
        return mk("newest_download", open=not t.startswith("show"))
    m = (F(r"(?:find|search for|locate|look for|where(?:'s| is| are))(?: my| the| a)? (?:files?|folders?|documents?) (?:called |named |with |containing |about |for |titled )?(.+)", t)
         or F(r"search (?:my |the )?(?:files|computer|pc|documents|downloads) for (.+)", t)
         or F(r"(?:find|locate)(?: my| the)? (.+?) (?:file|folder|document|doc)s?", t)
         or F(r"where(?:'s| is| did i (?:put|save)) (?:my |the )?(.+?) (?:file|folder|document)", t))
    if m:
        q = m.group(1).strip(" .")
        q = re.sub(r"^(?:called|named) ", "", q)
        if q and len(q) <= 60:
            return mk("find_file", query=q)

    # ---------------- system status ----------------
    if F(r"(?:what(?:'s| is) (?:my |the )?)?(?:cpu|processor)(?: usage| use| load| at| percent)?|how(?:'s| is| hard is) (?:my |the )?(?:cpu|processor)(?: doing| working)?|(?:check|show) (?:my |the )?cpu(?: usage)?", t):
        return mk("cpu")
    if F(r"(?:what(?:'s| is) (?:my |the )?)?(?:ram|memory)(?: usage| use| at)?|how much (?:ram|memory)(?: (?:am i|is being|is) (?:using|used|left|free|in use))?(?: do i have(?: left| free)?)?|(?:check|show) (?:my |the )?(?:ram|memory)(?: usage)?", t):
        return mk("ram")
    if F(r"(?:how much )?(?:free |disk |storage |hard drive |drive |ssd )(?:space|storage)(?: do i have| is left| left| is free| have i got)?(?: left)?|"
         r"how much (?:space|storage|disk space|room|free space)(?: do i have| is there| is| have i got)?(?: left| free)?(?: on (?:my |the )?(?:pc|computer|drive|disk|c drive|ssd))?|"
         r"(?:check|show) (?:my |the )?(?:disk|storage|drives?)(?: space)?|(?:disk|storage) (?:space|usage)|am i (?:running )?out of (?:space|storage)", t):
        return mk("disk")
    if F(r"(?:what(?:'s| is) (?:my |the )?)?battery(?: level| life| percent(?:age)?)?|how much battery(?: do i have| is left| left)?|(?:check|show) (?:my |the )?battery", t):
        return mk("battery")
    if F(r"(?:what(?:'s| is) (?:my |the )?)?(?:system )?uptime|how long (?:has|have) (?:my |the |this )?(?:pc|computer|system) been (?:on|running|up)|when did i (?:last )?(?:restart|reboot|turn on)(?: my| the)?(?: pc| computer)?", t):
        return mk("uptime")
    if F(r"what(?:'s| is) (?:using|eating|hogging|taking up|slowing down)(?: up)?(?: all)? (?:my |the )?(?:cpu|ram|memory|pc|computer|resources)|what(?:'s| is) slowing (?:down )?(?:my |the )?(?:pc|computer)(?: down)?|"
         r"(?:top|biggest|heaviest) (?:processes|apps)|why is my (?:pc|computer) (?:so )?(?:slow|laggy|lagging)", t):
        return mk("top_procs")
    if F(r"what(?:'s| is) my (?:local |pc'?s? )?ip(?: address)?|(?:my |local )?ip address", t):
        return mk("ip")
    if F(r"(?:am i|are we|is (?:my |the )?(?:pc|computer)) (?:online|connected)(?: to the internet)?|(?:is|check)(?: my| the)? (?:internet|wifi|wi-fi|connection|network)(?: (?:working|up|down|ok|okay|connected))?|"
         r"(?:what(?:'s| is) my |check (?:my )?)?(?:ping|latency)|(?:test|check) (?:my |the )?(?:internet|connection|network)(?: connection)?|is the internet (?:down|working|up)", t):
        return mk("net_check")

    # ---------------- clipboard / editing keys / typing ----------------
    if F(r"(?:read|say|tell me|what(?:'s| is)(?: on| in)?)(?: me)?(?: what(?:'s| is)(?: on| in))? (?:my |the )?clipboard|what did i (?:just )?copy|read (?:back )?what i copied", t):
        return mk("clip_read")
    if F(r"(?:clear|empty|wipe|erase) (?:my |the )?clipboard", t):
        return mk("clip_clear")
    if F(r"(?:google|search(?: for)?|look up) (?:what(?:'s| is) (?:on|in) )?(?:my |the )?clipboard|(?:google|search) what i copied", t):
        return mk("clip_search")
    for pat, name in KEYMAP:
        if F(pat, t):
            return mk("keys", name=name)
    m = F(r"(?:type|type out|dictate)(?: this| the following| out)?[:,]? (.+)", t)
    if m and len(m.group(1).split()) <= 40 and not re.match(r"(?:of |a message|an email|to )", m.group(1)):
        return mk("type_text", text=m.group(1))

    # ---------------- windows / desktop ----------------
    if F(r"show(?: me)? (?:the |my )?desktop|(?:minimi[sz]e|hide) (?:everything|all(?: (?:the |my )?windows)?|all apps)|go to (?:the )?desktop", t):
        return mk("win", op="desktop")
    if F(r"(?:minimi[sz]e|shrink) (?:this|it|that|this window|that window|the current window|the active window|current window)", t):
        return mk("win", op="minimize")
    if F(r"(?:maximi[sz]e) (?:this|it|that|this window|the current window|the active window|current window)|make (?:this|it|the window) (?:full ?screen|bigger|big)", t):
        return mk("win", op="maximize")
    if F(r"(?:restore|un ?maximi[sz]e) (?:this|it|this window)", t):
        return mk("win", op="restore")
    if F(r"(?:snap|move|put) (?:this|it|the window|this window) (?:to (?:the )?)?left(?: side)?|snap left", t):
        return mk("win", op="snap_left")
    if F(r"(?:snap|move|put) (?:this|it|the window|this window) (?:to (?:the )?)?right(?: side)?|snap right", t):
        return mk("win", op="snap_right")
    if F(r"(?:move|put|send) (?:this|it|the window|this window) to (?:my |the )?(?:other|next|second) (?:monitor|screen|display)", t):
        return mk("win", op="other_monitor")
    if F(r"(?:close|exit) (?:this|that|the current|the active|current|active) (?:window|app|program)", t):
        return mk("win", op="close")
    if F(r"(?:switch|change|swap) (?:windows?|apps?)|alt tab|(?:go (?:back )?to (?:the )?)?(?:last|previous) (?:window|app)|switch back", t):
        return mk("win", op="switch")
    if F(r"(?:show|open)(?: me)? (?:all )?(?:my )?(?:open windows|task view)|task view", t):
        return mk("win", op="task_view")
    if F(r"(?:new|create|make|add)(?: a)?(?: new)? (?:virtual )?desktop", t):
        return mk("win", op="desk_new")
    if F(r"(?:next|switch to (?:the )?next|go to (?:the )?next) (?:virtual )?desktop", t):
        return mk("win", op="desk_next")
    if F(r"(?:previous|switch to (?:the )?previous|go to (?:the )?previous) (?:virtual )?desktop", t):
        return mk("win", op="desk_prev")
    if F(r"close (?:this|the current) (?:virtual )?desktop", t):
        return mk("win", op="desk_close")
    if F(r"(?:open|show)(?: me)? (?:my |the )?(?:notifications?|notification (?:center|centre|panel)|action (?:center|centre))|notifications", t):
        return mk("win", op="notifications")
    if F(r"(?:open|show)(?: me)? (?:the )?quick settings|quick settings", t):
        return mk("win", op="quick_settings")

    # ---------------- power (always asks first) ----------------
    if F(r"(?:restart|reboot)(?: the| my)? (?:pc|computer|system|windows|machine)(?: now)?|reboot", t):
        return mk("restart_pc")
    if F(r"(?:sign|log) (?:me )?(?:out|off)(?: of windows| of (?:the|my) (?:pc|computer))?", t):
        return mk("sign_out")
    if F(r"hibernate(?: the| my)?(?: pc| computer)?|put (?:the |my )?(?:pc|computer) (?:in|into) hibernat(?:e|ion)", t):
        return mk("hibernate")
    if F(r"(?:cancel|abort|stop)(?: the)? (?:shut ?down|restart|reboot)|don'?t (?:shut ?down|restart)", t):
        return mk("abort_shutdown")
    if F(r"(?:empty|clear|clean out|clean)(?: out)? (?:the |my )?(?:recycle ?bin|trash|bin)", t):
        return mk("empty_bin")

    # ---------------- Windows dark / light mode ----------------
    m = F(r"(?:turn on |switch to |set |enable |use |go |make )?(?:windows |the pc |my pc )?(dark|light) mode(?: on)?(?: for windows| on windows)?|(?:turn|switch) (?:on|to) (?:windows )?(dark|light) mode|make windows (dark|light)", t)
    if m:
        return mk("theme_mode", mode=next(g for g in m.groups() if g))
    m = F(r"(?:turn off|disable) (?:windows )?(dark|light) mode|(?:windows )?(dark|light) mode off", t)
    if m:
        return mk("theme_mode", mode="light" if (m.group(1) or m.group(2)) == "dark" else "dark")

    # ---------------- quick sites & system tools ----------------
    m = F(r"(?:open|launch|start|pull up|bring up|go to|take me to|load)(?: up)? (.+)", t)
    if m:
        name = m.group(1).strip()
        mm = F(r"(?:the |my )?([a-z0-9][a-z0-9-]{0,40}(?:(?: dot |\.)[a-z0-9-]{1,40})?)(?: dot |\.)(com|net|org|gg|tv|io|co|us|edu|gov|ai|app|dev|wiki)", name)
        if mm:             # "go to roblox dot com": a spoken web address
            host = re.sub(r" dot ", ".", mm.group(1)).replace(" ", "") + "." + mm.group(2)
            return mk("open_domain", url="https://" + host + "/")
        if not _exact_app(name, app_names):        # an app he named exactly in his own list always wins
            k2, v = _alias_match(name, SYSTEM)
            if k2:
                return mk("open_system", key=k2)
            from .commands import _match_app
            if not _match_app(name, app_names)[0]:
                k2, v = _alias_match(name, SITES)
                if k2:
                    return mk("open_site", key=k2)

    # ---------------- quick answers ----------------
    if F(r"(?:flip|toss) a coin|heads or tails|coin (?:flip|toss)", t):
        return mk("coin")
    m = F(r"roll (?:a |an |the |(" + NUM + r") )?(?:dice|die|d ?(\d+))", t)
    if m:
        n = words_to_number(m.group(1)) if m.group(1) else 1
        sides = int(m.group(2) or 6)
        return mk("dice", n=max(1, min(int(n or 1), 10)), sides=max(2, min(sides, 1000)))
    m = F(r"(?:pick|give me|choose|generate) (?:a )?random number(?: between| from) (" + NUM + r") (?:and|to) (" + NUM + r")", t)
    if m:
        a, b = words_to_number(m.group(1)), words_to_number(m.group(2))
        if a is not None and b is not None:
            return mk("random_number", lo=int(min(a, b)), hi=int(max(a, b)))
    m = F(r"how (?:many days|long|many sleeps) (?:is it )?(?:until|till|til|before|to) (.+?)", t)
    if m:
        k2, v = _alias_match(m.group(1), HOLIDAYS)
        if k2:
            return mk("days_until", key=k2)
    if re.match(r"(?:what(?:'s| is)|whats|calculate|compute|how much is)\s", t) or re.search(r"\d", t):
        v = calculate(t)
        if v is not None:
            return mk("math", value=v, expr=t)

    # ---------------- Tab's progress window ----------------
    if F(r"(?:show|open|pull up|bring up|display)(?: me)? (?:the |your |tab'?s |my )?(?:progress|tasks?|task list|work)(?: window| panel| screen)?|"
         r"(?:what are you|what'?s tab|what is tab|what are we) (?:working on|doing|busy with)(?: right now| for me)?|"
         r"(?:how(?:'s| is) (?:the |my |tab'?s )?(?:progress|work) (?:going|coming along)|(?:check|show) (?:on )?(?:tab'?s )?progress)|progress report|tab'?s progress", t):
        return mk("progress_show")

    # ---------------- help by category ----------------
    m = (F(r"(?:help(?: me)?(?: with)?|what are (?:the|your)|show(?: me)?(?: the| your)?|list(?: the| your)?) ([a-z ]+?) commands?", t)
         or F(r"help(?: me)? with ([a-z ]+)", t) or F(r"what (?:can you do|commands are there|can i say) (?:with|for|about) ([a-z ]+)", t))
    if m:
        c = help_category(m.group(1))
        if c:
            return mk("help_category", category=c)
    return None

def _exact_app(name, app_names):
    n = re.sub(r"^(?:the|my|up)\s+", "", (name or "").lower()).strip(" .")
    n = re.sub(r"\s+(?:app|application|program)$", "", n).strip()
    for k in app_names:
        if n in [a.strip() for a in k.lower().split("|")] or n.replace(" ", "") in [a.strip().replace(" ", "") for a in k.lower().split("|")]:
            return k
    return None

def _number_ok(s):
    v = words_to_number(s)
    return v is not None and 0 <= v <= 100

COMMON_AUDIO_APPS = ("discord", "spotify", "chrome", "obs", "steam", "firefox", "edge", "roblox", "terraria", "youtube music", "vlc", "brave", "opera", "opera gx")
def _known_app_word(name, app_names):
    """The app's process-ish name if he named an app we know, else None (so "mute everything" stays master mute)."""
    n = (name or "").strip().lower()
    n = re.sub(r"^(?:the|my)\s+", "", n)
    if n in ("volume", "sound", "audio", "pc", "computer", "it", "everything", "master", "system", "main", "overall", "speakers", "speaker", "the", "my"):
        return None
    from .commands import _match_app
    key, _ = _match_app(n, app_names)
    if key:
        return key.split("|")[0]
    return n if n in COMMON_AUDIO_APPS else None

# =====================================================================================
#  help catalogue (shown in the workspace log; also spoken per category)
# =====================================================================================
CATALOG = [
    ("Timers & alarms", ["set a timer for 10 minutes", "set a pizza timer for 12 minutes", "how much time is left", "cancel the timer",
                         "set an alarm for 7:30 am", "wake me up at 7", "what alarms do I have", "cancel my alarm", "stop the alarm / snooze",
                         "remind me in 20 minutes to check the oven", "remind me at 5 pm to call grandma", "cancel my reminders",
                         "start / stop the stopwatch", "check the stopwatch"]),
    ("Volume & media", ["volume 40 / volume up / volume down", "mute / unmute", "what's the volume", "set Discord volume to 30",
                        "mute Spotify / unmute Discord", "pause / play / next song / previous song", "what's playing"]),
    ("Apps & websites", ["open Spotify / Discord / OBS / Terraria …", "open YouTube / Gmail / Twitch / GitHub / ChatGPT …",
                         "go to roblox dot com", "open task manager / calculator / settings", "open sound / Bluetooth / display settings",
                         "open Windows Update"]),
    ("Screenshots", ["take a screenshot", "snip (pick an area)", "open my screenshots"]),
    ("Files & folders", ["open downloads / documents / desktop / videos", "open my latest download", "find my homework file",
                         "search my files for resume"]),
    ("System status", ["CPU usage", "how much RAM am I using", "how much disk space is left", "battery", "uptime",
                       "what's slowing down my PC", "what's my IP", "am I online / check my ping", "status report"]),
    ("Clipboard & typing", ["read my clipboard", "clear my clipboard", "google my clipboard", "copy / paste / undo / redo / select all",
                            "save it", "type hello there", "new tab / close tab / reopen tab / refresh", "zoom in / zoom out", "emoji panel"]),
    ("Windows & desktop", ["show desktop", "minimize this / maximize this", "snap left / snap right", "move this to my other monitor",
                           "close this window", "switch windows / task view", "new desktop / next desktop", "open notifications / quick settings",
                           "Windows dark mode / Windows light mode"]),
    ("Power (asks first)", ["lock the PC", "screen off", "goodnight (sleep)", "restart the PC", "shut down the PC", "sign out", "hibernate",
                            "cancel the restart", "empty the recycle bin"]),
    ("Quick answers", ["what's 15 percent of 80", "what's 12 times 7", "flip a coin", "roll a d20", "pick a random number between 1 and 100",
                       "how many days until Christmas", "how long until Halloween", "what time is it / what's the date", "weather"]),
    ("JARVIS himself", ["show progress / what are you working on", "what can you do", "help with timers", "open workspace / close the app", "purple theme / next theme", "focus mode",
                        "what am I looking at", "ask Copilot …", "use my speakers", "open my log", "never mind", "go to sleep / wake up / close JARVIS"]),
]
CAT_WORDS = {"timer": 0, "timers": 0, "alarm": 0, "alarms": 0, "reminder": 0, "reminders": 0, "stopwatch": 0,
             "volume": 1, "sound": 1, "audio": 1, "music": 1, "media": 1, "app": 2, "apps": 2, "website": 2, "websites": 2, "sites": 2,
             "screenshot": 3, "screenshots": 3, "file": 4, "files": 4, "folder": 4, "folders": 4, "system": 5, "pc": 5, "computer": 5, "status": 5,
             "clipboard": 6, "typing": 6, "keyboard": 6, "window": 7, "windows": 7, "desktop": 7, "power": 8, "shutdown": 8, "restart": 8,
             "math": 9, "fun": 9, "answers": 9, "jarvis": 10, "yourself": 10}

def help_category(word):
    w = re.sub(r"^(?:the|my|your)\s+", "", (word or "").lower().strip())
    if w in CAT_WORDS: return CATALOG[CAT_WORDS[w]][0]
    for part in w.split():
        if part in CAT_WORDS: return CATALOG[CAT_WORDS[part]][0]
    return None

def help_lines():
    return [title.upper() + ": " + " · ".join(items) for title, items in CATALOG] + ["Anything else goes to Tab"]

def command_count():
    return sum(len(i) for _, i in CATALOG)

# =====================================================================================
#  Windows hands (the only part that touches the OS). Tests swap in a fake.
# =====================================================================================
class Hands:
    def __init__(self, cfg=None, data_dir=None):
        self.cfg = cfg or {}
        self.data_dir = data_dir

    def open_uri(self, target):
        """A URL, an ms-settings:/shell: page, or a built-in tool (several separated by |, first that works)."""
        if target.startswith("https://"):
            import webbrowser; webbrowser.open(target); return True
        if not IS_WIN:
            log.info("open %s (not Windows)", target); return False
        for cand in target.split("|"):
            try:
                if cand.endswith(".exe"):
                    subprocess.Popen([cand]); return True
                os.startfile(cand); return True
            except Exception as e:
                log.info("open %s failed: %r", cand, e)
        return False

    def open_path(self, path):
        if IS_WIN:
            os.startfile(path); return True
        log.info("open %s (not Windows)", path); return False

    def reveal(self, path):
        """Show a file selected in Explorer (never runs it)."""
        if IS_WIN:
            subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)]); return True
        log.info("reveal %s (not Windows)", path); return False

    def known_folder(self, name):
        home = os.path.expanduser("~")
        if name == "Home": return home
        if name == "AppData": return os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        if name == "JARVIS": return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if name == "JARVIS data": return self.data_dir or os.path.join(os.environ.get("LOCALAPPDATA", home), "JARVIS-data")
        if name == "Screenshots": return os.path.join(self.known_folder("Pictures"), "Screenshots")
        if name == "Captures": return os.path.join(self.known_folder("Videos"), "Captures")
        if IS_WIN:
            guid = {"Downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
                    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
                    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}", "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}"}.get(name)
            if guid:   # OneDrive-redirected Documents/Desktop/Pictures are found this way
                try:
                    import ctypes, uuid
                    from ctypes import wintypes
                    class GUID(ctypes.Structure):
                        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
                    u = uuid.UUID(guid)
                    g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
                    p = ctypes.c_wchar_p()
                    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
                        path = p.value; ctypes.windll.ole32.CoTaskMemFree(p)
                        return path
                except Exception:
                    log.exception("known folder %s", name)
        for base in (home, os.path.join(home, "OneDrive")):
            p = os.path.join(base, name)
            if os.path.isdir(p): return p
        return os.path.join(home, name)

    def keys(self, spec):
        from . import winctl
        winctl.press(spec); return True

    def type_text(self, text):
        from . import winctl
        winctl.type_text(text); return True

    def clip_get(self):
        if not IS_WIN:
            raise RuntimeError("clipboard: Windows only")
        import win32clipboard as wc
        wc.OpenClipboard()
        try:
            if wc.IsClipboardFormatAvailable(13):    # CF_UNICODETEXT
                return wc.GetClipboardData(13)
            if wc.IsClipboardFormatAvailable(15): return "\x00files"
            if wc.IsClipboardFormatAvailable(8) or wc.IsClipboardFormatAvailable(2): return "\x00image"
            return ""
        finally:
            wc.CloseClipboard()

    def clip_clear(self):
        if not IS_WIN:
            raise RuntimeError("clipboard: Windows only")
        import win32clipboard as wc
        wc.OpenClipboard()
        try: wc.EmptyClipboard()
        finally: wc.CloseClipboard()
        try:   # Windows 10/11 clipboard history (Win+V) too
            from . import winctl
            winctl.powershell("try { Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
                              "[Windows.ApplicationModel.DataTransfer.Clipboard,Windows.ApplicationModel.DataTransfer,ContentType=WindowsRuntime] | Out-Null; "
                              "[Windows.ApplicationModel.DataTransfer.Clipboard]::ClearHistory() | Out-Null } catch {}", timeout=15)
        except Exception:
            pass
        return True

    WIN_HOTKEYS = {"desktop": "win+d", "snap_left": "win+left", "snap_right": "win+right", "other_monitor": "win+shift+right",
                   "task_view": "win+tab", "desk_new": "win+ctrl+d", "desk_next": "win+ctrl+right", "desk_prev": "win+ctrl+left",
                   "desk_close": "win+ctrl+f4", "notifications": "win+n", "quick_settings": "win+a", "switch": "alt+tab"}
    def window(self, op):
        from . import winctl
        if op in self.WIN_HOTKEYS:
            winctl.press(self.WIN_HOTKEYS[op]); return True
        if not IS_WIN:
            raise RuntimeError("Windows only")
        import win32gui, win32con
        h = win32gui.GetForegroundWindow()
        cls = win32gui.GetClassName(h) if h else ""
        if not h or cls in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False                     # never WM_CLOSE the desktop: that pops up "Shut Down Windows"
        title = win32gui.GetWindowText(h)
        if "J.A.R.V.I.S." in title:
            return False
        if op == "minimize": win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
        elif op == "maximize": win32gui.ShowWindow(h, win32con.SW_MAXIMIZE)
        elif op == "restore": win32gui.ShowWindow(h, win32con.SW_RESTORE)
        elif op == "close": win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)   # polite: the app can still ask to save
        return title or True

    MASTER_CS = r"""
using System; using System.Runtime.InteropServices;
namespace JarvisMaster {
  [Guid("BCDE0395-E52F-467C-8E3D-C4579291692E"), ComImport] class MMDeviceEnumeratorCo { }
  [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDeviceEnumerator { int NotImpl1(); [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice); }
  [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDevice { [PreserveSig] int Activate(ref Guid iid, int dwClsCtx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o); }
  [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioEndpointVolume {
    int f1(); int f2(); int f3(); int f4();
    [PreserveSig] int SetMasterVolumeLevelScalar(float level, ref Guid ctx);
    int f6();
    [PreserveSig] int GetMasterVolumeLevelScalar(out float level);
    int f8(); int f9(); int f10(); int f11();
    [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid ctx);
    [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
  }
  public static class Vol {
    public static string Do(int level, int mute) {
      var en = (IMMDeviceEnumerator)(new MMDeviceEnumeratorCo()); IMMDevice dev; en.GetDefaultAudioEndpoint(0, 1, out dev);
      Guid iid = typeof(IAudioEndpointVolume).GUID; object o; dev.Activate(ref iid, 23, IntPtr.Zero, out o);
      var v = (IAudioEndpointVolume)o; Guid g = Guid.Empty;
      if (level >= 0) v.SetMasterVolumeLevelScalar(Math.Max(0, Math.Min(100, level)) / 100f, ref g);
      if (mute >= 0) v.SetMute(mute == 1, ref g);
      float lv; v.GetMasterVolumeLevelScalar(out lv); bool m; v.GetMute(out m);
      return "VOL " + ((int)Math.Round(lv * 100)) + (m ? " muted" : "");
    }
  }
}
"""
    def master(self, level=None, mute=None):
        """Default speakers: returns (level 0-100, muted). Exact, unlike the media keys."""
        from . import winctl
        lv = -1 if level is None else int(level); mu = -1 if mute is None else (1 if mute else 0)
        cs = self.MASTER_CS.replace("'", "''")
        code, out = winctl.powershell(f"Add-Type -TypeDefinition '{cs}' -Language CSharp; [JarvisMaster.Vol]::Do({lv}, {mu})", timeout=25)
        m = re.search(r"VOL (\d+)( muted)?", out or "")
        if code != 0 or not m:
            raise RuntimeError((out or "").strip()[:300] or f"exit {code}")
        return int(m.group(1)), bool(m.group(2))

    def app_volume(self, app, level=None, mute=None):
        from . import winctl
        return winctl.app_volume(app, level=level, mute=mute)

    def mute_key(self):
        from . import actions
        actions.volume("mute"); return True

    def power(self, op):
        if not IS_WIN:
            raise RuntimeError("Windows only")
        cmd = {"restart_pc": ["shutdown", "/r", "/t", "15"], "sign_out": ["shutdown", "/l"], "hibernate": ["shutdown", "/h"],
               "abort_shutdown": ["shutdown", "/a"]}[op]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        return p.returncode == 0, (p.stdout + p.stderr).strip()

    def bin_count(self):
        if not IS_WIN:
            return None
        import ctypes
        from ctypes import wintypes
        class SHQUERYRBINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]
        q = SHQUERYRBINFO(); q.cbSize = ctypes.sizeof(q)
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(q)) == 0:
            return int(q.i64NumItems), int(q.i64Size)
        return None

    def empty_bin(self):
        if not IS_WIN:
            raise RuntimeError("Windows only")
        import ctypes
        r = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)   # we already asked: no dialog, no progress, no sound
        return r in (0, -2147418113)      # S_OK, or E_UNEXPECTED when it was already empty

    def theme_mode(self, mode):
        if not IS_WIN:
            raise RuntimeError("Windows only")
        import winreg
        light = 1 if mode == "light" else 0
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        winreg.SetValueEx(k, "AppsUseLightTheme", 0, winreg.REG_DWORD, light)
        winreg.SetValueEx(k, "SystemUsesLightTheme", 0, winreg.REG_DWORD, light)
        winreg.CloseKey(k)
        try:
            import ctypes
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 2, 3000, None)
        except Exception:
            pass
        return True

    def screenshot(self):
        """Win+PrtScn: Windows itself saves a PNG of all screens to Pictures\\Screenshots."""
        from . import winctl
        winctl.press("win+printscreen"); return True

    def snip(self):
        from . import winctl
        winctl.press("win+shift+s"); return True

    def now_playing(self):
        from . import wsdata
        return wsdata.now_playing()

# =====================================================================================
#  portable helpers (psutil / filesystem / sockets): these also run, and are tested, on Linux
# =====================================================================================
def stats_cpu(interval=0.6):
    import psutil
    return psutil.cpu_percent(interval=interval)

def stats_ram():
    import psutil
    v = psutil.virtual_memory()
    return v.percent, v.used / 2**30, v.total / 2**30

def stats_disks():
    import psutil
    out, seen = [], set()
    for p in psutil.disk_partitions(all=False):
        if IS_WIN and ("cdrom" in p.opts or not p.fstype):
            continue
        if not IS_WIN and not (p.mountpoint == "/" or p.mountpoint.startswith(("/home", "/mnt", "/media"))):
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except Exception:
            continue
        name = p.device.rstrip("\\").rstrip(":") if IS_WIN else p.mountpoint
        if name in seen: continue
        seen.add(name)
        out.append((name, u.free / 2**30, u.total / 2**30, u.percent))
    if not out:
        u = psutil.disk_usage(os.path.abspath(os.sep))
        out.append((os.path.abspath(os.sep), u.free / 2**30, u.total / 2**30, u.percent))
    return out

def stats_battery():
    import psutil
    try:
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is None:
        return None
    left = None if b.secsleft in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) else b.secsleft
    return int(round(b.percent)), bool(b.power_plugged), left

def stats_uptime():
    import psutil
    return time.time() - psutil.boot_time()

def top_procs(n=3, sample=0.5):
    import psutil
    procs = []
    for p in psutil.process_iter(["name", "pid"]):
        try:
            p.cpu_percent(None); procs.append(p)
        except Exception:
            pass
    time.sleep(sample)
    rows = {}
    ncpu = psutil.cpu_count() or 1
    for p in procs:
        try:
            name = p.info["name"] or "?"
            if name.lower() in ("system idle process", "idle", "system", "memory compression", "registry"):
                continue
            c = p.cpu_percent(None) / ncpu; m = p.memory_info().rss
        except Exception:
            continue
        r = rows.setdefault(name, [0.0, 0])
        r[0] += c; r[1] += m
    by_cpu = sorted(rows.items(), key=lambda kv: -kv[1][0])[:n]
    by_ram = sorted(rows.items(), key=lambda kv: -kv[1][1])[:n]
    return by_cpu, by_ram

def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))     # UDP connect sends nothing; it just picks the outward interface
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()

def net_check(hosts=(("1.1.1.1", 443), ("8.8.8.8", 53), ("www.google.com", 443)), timeout=2.0):
    """(online, best connect latency in ms). A TCP handshake only; no data is sent."""
    best = None
    for h, port in hosts:
        t0 = time.perf_counter()
        try:
            with socket.create_connection((h, port), timeout=timeout):
                ms = (time.perf_counter() - t0) * 1000
                best = ms if best is None else min(best, ms)
        except OSError:
            continue
    return best is not None, best

def human_size(n):
    for u in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u in ("bytes", "KB") else f"{n:.1f} {u}"
        n /= 1024

def newest_file(folder):
    """Newest finished file in a folder (skips partial downloads, hidden and system files)."""
    best = None
    try:
        with os.scandir(folder) as it:
            for e in it:
                if e.name.startswith((".", "~$")) or e.name.lower() in ("desktop.ini", "thumbs.db"):
                    continue
                if os.path.splitext(e.name)[1].lower() in PARTIAL_EXT:
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                if best is None or st.st_mtime > best[1]:
                    best = (e.path, st.st_mtime)
    except OSError:
        return None
    return best[0] if best else None

def is_risky(path):
    return os.path.splitext(path)[1].lower() in RISKY_EXT

def find_files(query, roots, limit=20, max_dirs=6000, seconds=4.0, max_depth=6):
    """Name search under his own folders: every word must be in the name. Newest first. Bounded in time and size."""
    q = re.sub(r"\b(?:file|files|folder|document|doc|the|my|a|called|named)\b", " ", (query or "").lower())
    words = [w for w in re.split(r"[\s_\-.]+", q) if w]
    if not words:
        return [], False
    deadline = time.time() + seconds
    hits, dirs, truncated = [], 0, False
    skip = {"appdata", "node_modules", "$recycle.bin", ".git", "__pycache__", "windows", ".cache", "venv", ".venv"}
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dp, dn, fn in os.walk(root):
            dirs += 1
            if dirs > max_dirs or time.time() > deadline:
                truncated = True; break
            if dp.count(os.sep) - base_depth >= max_depth:
                dn[:] = []
            dn[:] = [d for d in dn if d.lower() not in skip and not d.startswith(".")]
            for name in fn + dn:
                if all(w in name.lower() for w in words):
                    p = os.path.join(dp, name)
                    try:
                        hits.append((p, os.path.getmtime(p)))
                    except OSError:
                        pass
        if truncated:
            break
    hits.sort(key=lambda h: -h[1])
    seen, out = set(), []
    for p, _ in hits:
        if p not in seen:
            seen.add(p); out.append(p)
    return out[:limit], truncated

def days_until(month, day, today=None):
    today = today or datetime.date.today()
    d = datetime.date(today.year, month, day)
    if d < today:
        d = datetime.date(today.year + 1, month, day)
    return (d - today).days

# =====================================================================================
#  timers, alarms, reminders, stopwatch
# =====================================================================================
class Clock:
    """One background thread rings due jobs. now() and the thread are injectable for tests.
    Alarms, reminders and timers are saved to disk, so a restart doesn't lose them."""
    def __init__(self, on_ring, path=None, now=time.time, start=True, repeat=2, repeat_gap=45):
        self.on_ring, self.path, self.now = on_ring, path, now
        self.jobs = []
        self.lock = threading.RLock()
        self.repeat, self.repeat_gap = repeat, repeat_gap
        self.ringing = None
        self.last_rung = None
        self.sw_start = None; self.sw_elapsed = 0.0
        self._n = 0
        self._load()
        if start:
            threading.Thread(target=self._run, name="jarvis-clock", daemon=True).start()

    def _run(self):
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("clock tick")
            time.sleep(0.5)

    def add(self, kind, at, label=None, total=None):
        with self.lock:
            self._n += 1
            j = {"id": f"{kind[0]}{int(self.now() * 1000) % 10**8}-{self._n}", "kind": kind, "at": float(at), "label": label,
                 "total": total, "rings": 0}
            self.jobs.append(j); self.jobs.sort(key=lambda x: x["at"]); self._save()
            return j

    def list(self, *kinds):
        with self.lock:
            return [dict(j) for j in self.jobs if not kinds or j["kind"] in kinds]

    def cancel(self, *kinds, all_=True):
        with self.lock:
            mine = [j for j in self.jobs if not kinds or j["kind"] in kinds]
            if not all_ and mine:
                mine = [min(mine, key=lambda j: j["at"])]
            for j in mine:
                self.jobs.remove(j)
            if self.ringing and (not kinds or self.ringing["kind"] in kinds):
                self.ringing = None
            self._save()
            return mine

    def stop_ringing(self):
        with self.lock:
            r, self.ringing = self.ringing, None
            if r is not None:
                self.jobs = [j for j in self.jobs if j["id"] != r["id"]]
                self._save()
            return r

    def snooze(self, seconds):
        with self.lock:
            r = self.ringing
            if r is None and self.last_rung and self.now() - self.last_rung.get("rang_at", 0) < 600:
                r = self.last_rung
            if r is None:
                return None
            self.ringing = None
            self.jobs = [j for j in self.jobs if j["id"] != r["id"]]
            return self.add(r["kind"], self.now() + seconds, r.get("label"), r.get("total"))

    def tick(self):
        now = self.now()
        due = []
        with self.lock:
            for j in list(self.jobs):
                if j.get("next", j["at"]) > now:
                    continue
                j["rings"] += 1
                late = now - j["at"] > 120 and j["rings"] == 1
                j["rang_at"] = now
                repeats = self.repeat if j["kind"] in ("alarm", "timer") else 0
                if j["rings"] > repeats:
                    self.jobs.remove(j)
                    if self.ringing and self.ringing["id"] == j["id"]:
                        self.ringing = None
                else:
                    j["next"] = now + self.repeat_gap
                    self.ringing = j
                self.last_rung = dict(j)
                due.append((dict(j), late))
            if due:
                self._save()
        for j, late in due:
            try:
                self.on_ring(j, late)
            except Exception:
                log.exception("ring")
        return [j for j, _ in due]

    def sw_go(self):
        if self.sw_start is not None:
            return False
        self.sw_start = self.now(); return True
    def sw_read(self):
        return self.sw_elapsed + ((self.now() - self.sw_start) if self.sw_start is not None else 0)
    def sw_halt(self):
        if self.sw_start is None:
            return None
        v = self.sw_elapsed + self.now() - self.sw_start
        self.sw_start = None; self.sw_elapsed = 0.0
        return v

    def _save(self):
        if not self.path:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.jobs, f)
            os.replace(tmp, self.path)
        except Exception:
            log.exception("save timers")

    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            now = self.now()
            for j in json.load(open(self.path, encoding="utf-8")):
                if j.get("at", 0) > now - 6 * 3600:      # anything more than 6 h stale is dropped
                    j["rings"] = 0; j.pop("next", None)
                    self.jobs.append(j)
            self.jobs.sort(key=lambda x: x["at"])
        except Exception:
            log.exception("load timers")

    def next_timer(self):
        with self.lock:
            t = [j for j in self.jobs if j["kind"] == "timer"]
            return dict(min(t, key=lambda j: j["at"])) if t else None

# =====================================================================================
#  the handler
# =====================================================================================
class QoL:
    def __init__(self, brain, hands=None, clock=None, state_dir=None, start_clock=True):
        self.b = brain
        self.cfg = (brain.cfg.get("quick", {}) or {})
        self.state_dir = state_dir
        self.hands = hands or Hands(brain.cfg, state_dir)
        if clock is None:
            path = os.path.join(state_dir, "timers.json") if state_dir else None
            clock = Clock(self._ring, path=path, repeat=int(self.cfg.get("alarm_repeats", 2)),
                          repeat_gap=float(self.cfg.get("alarm_repeat_seconds", 45)), start=start_clock)
        else:
            clock.on_ring = self._ring
        self.clock = clock
        self.sites = dict(SITES)
        for k, v in (self.cfg.get("sites") or {}).items():
            if isinstance(v, str) and v.startswith("https://"):
                self.sites[k.lower()] = v
        self.done = []          # (action, detail): what actually ran (tests read this)

    @property
    def s(self):
        return self.b.sir

    def say(self, text):
        return self.b.say(text)

    def _ui(self, name, *a):
        ui = self.b.ui
        if ui is not None and hasattr(ui, name):
            try: getattr(ui, name)(*a)
            except Exception: log.exception("ui %s", name)

    def _log(self, action, detail=""):
        self.done.append((action, detail))
        log.info("local %s %s", action, detail)
        if self.state_dir:
            try:
                with open(os.path.join(self.state_dir, "actions.log"), "a", encoding="utf-8") as f:
                    f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} LOCAL {action} {str(detail)[:200]}\n")
            except Exception:
                pass

    def _hand(self, name, *a):
        """Call one Windows hand. Returns (ok, value). The brain's dry-run mode only records it."""
        self._log(name, ", ".join(map(str, a))[:200])
        if getattr(self.b, "dry", False) and not getattr(self.hands, "is_fake", False):
            return True, None
        try:
            return True, getattr(self.hands, name)(*a)
        except Exception as e:
            log.warning("hand %s failed: %r", name, e)
            return False, e

    # ---------- ringing (from the clock thread) ----------
    def _ring(self, j, late=False):
        s = self.s
        label = j.get("label")
        if j["kind"] == "timer":
            what = f"Your {label} timer is done" if label else (f"Your {say_duration(j['total'])} timer is done" if j.get("total") else "Your timer is done")
            msg = f"{what}, {s}." if j["rings"] == 1 else f"{what}, {s}. Say stop the timer."
        elif j["kind"] == "alarm":
            hhmm = datetime.datetime.fromtimestamp(j["at"]).strftime("%I:%M %p").lstrip("0")
            msg = f"It's {hhmm}, {s}. Your alarm." if j["rings"] == 1 else f"Alarm, {s}. Say stop the alarm, or snooze."
        else:
            msg = f"Reminder, {s}: {label}." if label else f"Your reminder, {s}."
        if late:
            msg = "While I was offline: " + msg
        self._ui("sfx", "done")
        self._ui("timers_changed")
        self.say(msg)

    # ---------- dispatcher ----------
    def handle(self, it, ask=None):
        a, x = it.arg, it.extra
        if a in DESTRUCTIVE:
            return self._confirm(a, ask, from_guess=bool(x.get("fuzzy") or x.get("repaired")))
        fn = getattr(self, "_a_" + str(a), None)
        if fn is None:
            return self.say(f"I can't do that one yet, {self.s}.")
        return fn(x, ask)

    # ---------- confirmation for power actions ----------
    def _confirm(self, action, ask, from_guess=False):
        s = self.s
        if from_guess:
            return self.say(f"I won't guess on that one, {s}. Say it again clearly if you meant it.")
        q = CONFIRM_TEXT[action].format(s=s)
        if action == "empty_bin":
            ok, cnt = self._hand("bin_count")
            if ok and isinstance(cnt, tuple):
                if cnt[0] == 0:
                    return self.say(f"The Recycle Bin is already empty, {s}.")
                q = f"Permanently delete the {cnt[0]} item{'s' if cnt[0] != 1 else ''} in the Recycle Bin, {human_size(cnt[1])}, {s}? That can't be undone."
        ans = ask(q) if ask else ""
        if not ans:
            self.b.pending = ("qol:" + action, time.time())
            return self.say(f"Say yes when you're sure, {s}.") if ask else self.say(q + " Say yes to confirm.")
        from .commands import is_yes
        if is_yes(ans):
            return self.confirmed(action)
        return self.say(f"Very well, {s}. Nothing done.")

    def confirmed(self, action):
        s = self.s
        if action in ("restart_pc", "sign_out", "hibernate"):
            words = {"restart_pc": f"Restarting in fifteen seconds, {s}. Say cancel the restart to stop it.",
                     "sign_out": f"Signing you out, {s}.", "hibernate": f"Hibernating, {s}. Press the power button to resume."}[action]
            self.say(words)
            if action != "restart_pc" and hasattr(self.b.voice, "wait"):
                self.b.voice.wait(6)
            ok, r = self._hand("power", action)
            if not ok or (isinstance(r, tuple) and not r[0]):
                why = " Hibernate may be switched off on this PC." if action == "hibernate" else ""
                return self.say(f"Windows refused that, {s}.{why}")
            return words
        if action == "empty_bin":
            ok, r = self._hand("empty_bin")
            return self.say(f"Recycle Bin emptied, {s}." if ok and r is not False else f"I couldn't empty the Recycle Bin, {s}.")
        return self.say(f"Very good, {s}.")

    # ---------- timers / stopwatch / alarms / reminders ----------
    def _a_timer_set(self, x, ask):
        s = self.s
        sec = x.get("seconds")
        if not sec and ask:
            ans = ask(f"For how long, {s}?")
            sec = parse_duration(re.sub(r"^(?:for|about|uh|um|like)\s+", "", (ans or "").lower().strip(" .?!")))
            if not sec:
                return self.say(f"I didn't catch a time, {s}. Try: set a timer for 5 minutes.")
        if not sec:
            return self.say(f"For how long, {s}? Say: set a timer for 5 minutes.")
        if sec > 24 * 3600:
            return self.say(f"That's over a day, {s}. Try an alarm instead.")
        self.clock.add("timer", self.clock.now() + sec, x.get("label"), total=sec)
        self._log("timer_set", f"{sec}s {x.get('label') or ''}".strip())
        self._ui("timers_changed")
        lbl = f"{x['label']} timer" if x.get("label") else "Timer"
        return self.say(f"{lbl[0].upper() + lbl[1:]} set for {say_duration(sec)}, {s}.")

    def _a_timer_status(self, x, ask):
        s = self.s
        ts = self.clock.list("timer")
        if not ts:
            sw = self.clock.sw_start is not None
            return self.say(f"No timers running, {s}." + (" The stopwatch is." if sw else ""))
        now = self.clock.now()
        parts = []
        for j in ts[:4]:
            left = max(0, j["at"] - now)
            name = f"the {j['label']} timer" if j.get("label") else ("your timer" if len(ts) == 1 else f"the {say_duration(j['total'])} timer")
            parts.append(f"{say_duration(left)} left on {name}")
        return self.say(("; ".join(parts) + f", {s}.").capitalize() if len(parts) == 1 else "; ".join(parts) + f", {s}.")

    def _a_timer_cancel(self, x, ask):
        s = self.s
        ts = self.clock.list("timer")
        if not ts:
            return self.say(f"There's no timer running, {s}.")
        gone = self.clock.cancel("timer", all_=bool(x.get("all")) or len(ts) == 1)
        self._log("timer_cancel", len(gone)); self._ui("timers_changed")
        if len(gone) > 1:
            return self.say(f"All {len(gone)} timers cancelled, {s}.")
        left = len(ts) - len(gone)
        return self.say(f"Timer cancelled, {s}." + (f" {left} still running." if left else ""))

    def _a_stopwatch_start(self, x, ask):
        if not self.clock.sw_go():
            return self.say(f"The stopwatch is already running, {self.s}. {say_duration(self.clock.sw_read())} so far.")
        self._log("stopwatch_start")
        return self.say(f"Stopwatch started, {self.s}.")

    def _a_stopwatch_stop(self, x, ask):
        v = self.clock.sw_halt()
        if v is None:
            return self.say(f"The stopwatch isn't running, {self.s}.")
        self._log("stopwatch_stop", round(v, 1))
        exact = f"{v:.1f} seconds" if v < 60 else say_duration(v)
        return self.say(f"Stopwatch stopped at {exact}, {self.s}.")

    def _a_stopwatch_check(self, x, ask):
        if self.clock.sw_start is None:
            return self.say(f"The stopwatch isn't running, {self.s}. Say start a stopwatch.")
        v = self.clock.sw_read()
        return self.say(f"{(f'{v:.0f} seconds' if v < 60 else say_duration(v))}, {self.s}.")

    def _a_alarm_set(self, x, ask):
        s = self.s
        at = x.get("at") or (self.clock.now() + x["after"] if x.get("after") else None)
        if not at and ask:
            ans = ask(f"What time, {s}?")
            dt = parse_clock(re.sub(r"^(?:for|at|uh|um)\s+", "", (ans or "").lower().strip(" .?!")))
            at = dt.timestamp() if dt else None
            if not at:
                return self.say(f"I didn't catch a time, {s}. Try: set an alarm for 7 30 am.")
        if not at:
            return self.say(f"What time, {s}? Say: set an alarm for 7 am.")
        self.clock.add("alarm", at)
        dt = datetime.datetime.fromtimestamp(at)
        self._log("alarm_set", dt.isoformat(timespec="minutes")); self._ui("timers_changed")
        mins = int((at - self.clock.now()) // 60)
        ahead = f" That's {say_duration(mins * 60)} from now." if 0 < mins < 24 * 60 else ""
        return self.say(f"Alarm set for {say_clock(dt)}, {s}.{ahead} JARVIS needs to be running and the PC awake for it to ring.")

    def _a_alarm_list(self, x, ask):
        s = self.s
        js = self.clock.list("alarm", "remind")
        if not js:
            return self.say(f"No alarms or reminders set, {s}.")
        parts = []
        for j in js[:5]:
            dt = datetime.datetime.fromtimestamp(j["at"])
            parts.append((f"an alarm at {say_clock(dt)}" if j["kind"] == "alarm" else f"a reminder at {say_clock(dt)} to {j.get('label')}"))
        return self.say("You have " + ", ".join(parts) + f", {s}.")

    def _a_alarm_cancel(self, x, ask):
        gone = self.clock.cancel("alarm")
        self._log("alarm_cancel", len(gone)); self._ui("timers_changed")
        if not gone:
            return self.say(f"You don't have any alarms set, {self.s}.")
        return self.say(f"{'Alarm' if len(gone) == 1 else str(len(gone)) + ' alarms'} cancelled, {self.s}.")

    def _a_remind_cancel(self, x, ask):
        gone = self.clock.cancel("remind")
        self._log("remind_cancel", len(gone))
        if not gone:
            return self.say(f"You don't have any reminders set, {self.s}.")
        return self.say(f"{'Reminder' if len(gone) == 1 else str(len(gone)) + ' reminders'} cancelled, {self.s}.")

    def _a_ring_stop(self, x, ask):
        r = self.clock.stop_ringing()
        self._ui("timers_changed")
        if r is None:
            return self.say(f"Nothing's ringing, {self.s}.")
        self._log("ring_stop", r["kind"])
        return self.say(random.choice([f"Alarm off, {self.s}.", f"Silenced, {self.s}."]) if r["kind"] == "alarm" else f"Timer stopped, {self.s}.")

    def _a_snooze(self, x, ask):
        sec = x.get("seconds") or int(float(self.cfg.get("snooze_minutes", 9)) * 60)
        j = self.clock.snooze(sec)
        if j is None:
            return self.say(f"There's nothing to snooze, {self.s}.")
        self._log("snooze", sec); self._ui("timers_changed")
        return self.say(f"Snoozing for {say_duration(sec)}, {self.s}.")

    def _a_remind(self, x, ask):
        s = self.s
        what = (x.get("what") or "").strip()
        what = re.sub(r"^(?:to|that)\s+", "", what)
        at = x.get("at") or (self.clock.now() + x["after"] if x.get("after") else None)
        if not at and ask:
            ans = (ask(f"When should I remind you, {s}?") or "").lower().strip(" .?!")
            d = parse_duration(re.sub(r"^in\s+", "", ans))
            if d:
                at = self.clock.now() + d
            else:
                dt = parse_clock(re.sub(r"^(?:at|around)\s+", "", ans))
                at = dt.timestamp() if dt else None
            if not at:
                return self.say(f"I didn't catch when, {s}. Try: remind me in 20 minutes to {what or 'check the oven'}.")
        if not at:
            return self.say(f"When, {s}? Try: remind me in 20 minutes to {what or 'check the oven'}.")
        self.clock.add("remind", at, what)
        self._log("remind", f"{datetime.datetime.fromtimestamp(at):%H:%M} {what}"); self._ui("timers_changed")
        mine = re.sub(r"\bmy\b", "your", re.sub(r"\bme\b", "you", re.sub(r"\bi\b", "you", what)))
        mine = re.sub(r"\bi'm\b", "you're", mine)
        when = "in " + say_duration(at - self.clock.now()) if at - self.clock.now() < 6 * 3600 else "at " + say_clock(datetime.datetime.fromtimestamp(at))
        return self.say(f"I'll remind you {when} to {mine}, {s}.")

    # ---------- volume / media ----------
    def _a_volume_get(self, x, ask):
        ok, r = self._hand("master")
        if ok and isinstance(r, tuple):
            return self.say(f"Volume is at {r[0]} percent, {self.s}" + (", but muted." if r[1] else "."))
        return self.say(f"I couldn't read the volume, {self.s}.")

    def _a_mute(self, x, ask):
        on = bool(x.get("on"))
        ok, r = self._hand("master", None, on)
        if not ok:                                   # Core Audio unavailable: fall back to the mute key (a toggle)
            self._hand("mute_key")
            return self.say(f"Mute toggled, {self.s}.")
        return "" if on else self.say(f"Sound's back, {self.s}.")

    def _a_app_volume(self, x, ask):
        ok, r = self._hand("app_volume", x["app"], x["level"])
        if ok and isinstance(r, str) and "no audio session" in r:
            return self.say(f"{x['app'].title()} isn't making any sound right now, {self.s}, so Windows has no volume for it yet.")
        if not ok:
            return self.say(f"I couldn't change {x['app'].title()}'s volume, {self.s}.")
        return self.say(f"{x['app'].title()} volume {x['level']} percent, {self.s}.")

    def _a_app_mute(self, x, ask):
        ok, r = self._hand("app_volume", x["app"], None, bool(x["on"]))
        if ok and isinstance(r, str) and "no audio session" in r:
            return self.say(f"{x['app'].title()} isn't playing anything right now, {self.s}.")
        if not ok:
            return self.say(f"I couldn't reach {x['app'].title()}'s volume, {self.s}.")
        return self.say(f"{x['app'].title()} {'muted' if x['on'] else 'unmuted'}, {self.s}.")

    def _a_now_playing(self, x, ask):
        ok, r = self._hand("now_playing")
        if ok and isinstance(r, dict) and r.get("title"):
            by = f" by {r['artist']}" if r.get("artist") else ""
            return self.say(f"{r['title']}{by}, {self.s}.")
        return self.say(f"Nothing I can see is playing, {self.s}.")

    # ---------- screenshots ----------
    def _a_screenshot(self, x, ask):
        folder = self.hands.known_folder("Screenshots")
        before = newest_file(folder) if os.path.isdir(folder) else None
        ok, _ = self._hand("screenshot")
        if not ok:
            return self.say(f"The screenshot didn't work, {self.s}.")
        if getattr(self.b, "dry", False) or getattr(self.hands, "is_fake", False) or not IS_WIN:
            return self.say(f"Screenshot saved to Pictures, Screenshots, {self.s}.")
        for _ in range(12):            # Windows writes the PNG a moment later
            time.sleep(0.25)
            after = newest_file(folder) if os.path.isdir(folder) else None
            if after and after != before:
                return self.say(f"Screenshot saved to Pictures, Screenshots, {self.s}.")
        return self.say(f"Screenshot taken, {self.s}. If it's not in Pictures, Screenshots, it went to your clipboard.")

    def _a_snip(self, x, ask):
        self._hand("snip")
        return self.say(f"Drag over what you want, {self.s}. It goes to your clipboard.")

    # ---------- files & folders ----------
    def _a_open_folder(self, x, ask):
        name = x["folder"]
        path = self.hands.known_folder(name)
        if not os.path.isdir(path):
            return self.say(f"I can't find your {name} folder, {self.s}.")
        self._hand("open_path", path)
        nice = {"JARVIS data": "my data folder", "JARVIS": "my install folder", "Home": "your user folder"}.get(name, f"your {name.lower()}")
        return self.say(f"Here's {nice}, {self.s}." if not name.endswith("s") or name in ("JARVIS",) else f"Your {name.lower()}, {self.s}.")

    def _a_newest_download(self, x, ask):
        folder = self.hands.known_folder("Downloads")
        f = newest_file(folder)
        if not f:
            return self.say(f"Your Downloads folder is empty, {self.s}.")
        name = os.path.basename(f)
        if os.path.isdir(f) or is_risky(f) or not x.get("open", True):
            self._hand("reveal", f)
            why = f" It's a program, so I've only highlighted it. Double-click it yourself if you trust it." if is_risky(f) else ""
            return self.say(f"Your latest download is {name}, {self.s}.{why}")
        self._hand("open_path", f)
        return self.say(f"Opening {name}, {self.s}.")

    def _a_find_file(self, x, ask):
        q = x["query"]
        roots = [self.hands.known_folder(n) for n in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")]
        roots += [os.path.expandvars(r) for r in (self.cfg.get("search_roots") or [])]
        hits, truncated = find_files(q, roots, limit=int(self.cfg.get("find_limit", 20)))
        self._log("find_file", f"{q!r}: {len(hits)} hit(s)")
        if not hits:
            return self.say(f"I couldn't find anything named {q} in your folders, {self.s}." + (" I stopped early; it's a big search." if truncated else ""))
        if self.b.ui is not None and hasattr(self.b.ui, "show_lines"):
            self._ui("show_lines", f"Files matching \u201c{q}\u201d", hits[:10])
        self._hand("reveal", hits[0])
        if len(hits) == 1:
            return self.say(f"Found it, {self.s}: {os.path.basename(hits[0])}. I've highlighted it in Explorer.")
        return self.say(f"{len(hits)}{'+' if truncated or len(hits) >= 20 else ''} matches, {self.s}. The newest, {os.path.basename(hits[0])}, is highlighted. The list is in the workspace.")

    # ---------- system status ----------
    def _a_cpu(self, x, ask):
        try: v = stats_cpu()
        except Exception: return self.say(f"I can't read the CPU right now, {self.s}.")
        mood = "barely working" if v < 15 else "comfortable" if v < 60 else "working hard" if v < 90 else "maxed out"
        return self.say(f"CPU is at {v:.0f} percent, {self.s}. {mood.capitalize()}.")

    def _a_ram(self, x, ask):
        try: pct, used, total = stats_ram()
        except Exception: return self.say(f"I can't read memory right now, {self.s}.")
        return self.say(f"You're using {used:.1f} of {total:.0f} gigabytes of RAM, {pct:.0f} percent, {self.s}.")

    def _a_disk(self, x, ask):
        try: ds = stats_disks()
        except Exception: return self.say(f"I can't read the drives right now, {self.s}.")
        parts = []
        for name, free, total, pct in ds[:4]:
            label = f"{name} drive" if IS_WIN else name
            parts.append(f"{label}: {free:.0f} gigabytes free of {total:.0f}")
        low = [d for d in ds if d[1] < 20 or d[3] > 92]
        warn = f" {low[0][0]} is getting full." if low else ""
        return self.say("; ".join(parts) + f", {self.s}.{warn}")

    def _a_battery(self, x, ask):
        try: b = stats_battery()
        except Exception: b = None
        if b is None:
            return self.say(f"This PC doesn't have a battery, {self.s}. It's on wall power.")
        pct, plugged, left = b
        tail = " and charging" if plugged else (f", about {say_duration(left)} left" if left else "")
        return self.say(f"Battery at {pct} percent{tail}, {self.s}.")

    def _a_uptime(self, x, ask):
        try: up = stats_uptime()
        except Exception: return self.say(f"I can't tell, {self.s}.")
        d = int(up // 86400)
        tail = f"{d} day{'s' if d != 1 else ''} and {say_duration(up % 86400)}" if d else say_duration(up)
        hint = " A restart might do it good." if d >= 7 else ""
        return self.say(f"The PC has been on for {tail}, {self.s}.{hint}")

    def _a_top_procs(self, x, ask):
        try: by_cpu, by_ram = top_procs()
        except Exception: return self.say(f"I can't read the process list, {self.s}.")
        nm = lambda n: re.sub(r"\.exe$", "", n, flags=re.I)
        cpu = ", ".join(f"{nm(n)} {v[0]:.0f} percent" for n, v in by_cpu[:3] if v[0] >= 1) or "nothing much"
        ram = ", ".join(f"{nm(n)} {v[1] / 2**30:.1f} gigs" for n, v in by_ram[:3])
        self._ui("show_lines", "Heaviest apps", [f"CPU  {nm(n)}  {v[0]:.0f}%" for n, v in by_cpu] + [f"RAM  {nm(n)}  {v[1] / 2**30:.1f} GB" for n, v in by_ram])
        return self.say(f"Top CPU: {cpu}. Top memory: {ram}, {self.s}. Say close and the app name if you want one gone.")

    def _a_ip(self, x, ask):
        ip = local_ip()
        if not ip:
            return self.say(f"You don't seem to be on a network, {self.s}.")
        return self.say(f"Your local IP is {ip.replace('.', ' dot ')}, {self.s}.")

    def _a_net_check(self, x, ask):
        online, ms = net_check()
        if not online:
            return self.say(f"No internet, {self.s}. I can't reach Cloudflare or Google. Check the router or the cable.")
        q = "excellent" if ms < 30 else "fine" if ms < 80 else "a bit slow" if ms < 200 else "poor"
        return self.say(f"You're online, {self.s}. Ping about {ms:.0f} milliseconds, {q}.")

    # ---------- clipboard / keys / typing ----------
    def _a_clip_read(self, x, ask):
        ok, txt = self._hand("clip_get")
        if not ok:
            return self.say(f"I couldn't read the clipboard, {self.s}.")
        if txt == "\x00files": return self.say(f"Your clipboard has files on it, {self.s}, not text.")
        if txt == "\x00image": return self.say(f"Your clipboard has an image on it, {self.s}.")
        txt = (txt or "").strip()
        if not txt: return self.say(f"Your clipboard is empty, {self.s}.")
        if len(txt) > 220:
            return self.say(f"It's {len(txt.split())} words, {self.s}. It starts: {txt[:200].rsplit(' ', 1)[0]}…")
        return self.say(f"Your clipboard says: {txt}")

    def _a_clip_clear(self, x, ask):
        ok, _ = self._hand("clip_clear")
        return self.say(f"Clipboard cleared, {self.s}." if ok else f"I couldn't clear the clipboard, {self.s}.")

    def _a_clip_search(self, x, ask):
        ok, txt = self._hand("clip_get")
        txt = (txt or "").strip() if ok and isinstance(txt, str) and not txt.startswith("\x00") else ""
        if not txt:
            return self.say(f"There's no text on your clipboard to search, {self.s}.")
        import urllib.parse
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(txt[:300])
        self._hand("open_uri", url)
        return self.say(f"Searching for what you copied, {self.s}.")

    def _a_keys(self, x, ask):
        name = x["name"]
        ok, _ = self._hand("keys", KEYSPEC[name])
        if not ok:
            return self.say(f"That key press didn't go through, {self.s}.")
        return ""          # silent: he can see it happen (and speaking would be slower than the paste)

    def _a_type_text(self, x, ask):
        text = x["text"]
        ok, _ = self._hand("type_text", text)
        return "" if ok else self.say(f"I couldn't type there, {self.s}.")

    # ---------- windows / desktop ----------
    def _a_win(self, x, ask):
        op = x["op"]
        ok, r = self._hand("window", op)
        if not ok:
            return self.say(f"That didn't work, {self.s}.")
        if r is False:
            return self.say(f"There's no app window in front to do that to, {self.s}.")
        if op == "close":
            return self.say(f"Closed, {self.s}. If it had unsaved work, it'll ask you.")
        return ""

    # ---------- power that doesn't need a yes ----------
    def _a_abort_shutdown(self, x, ask):
        ok, r = self._hand("power", "abort_shutdown")
        if ok and isinstance(r, tuple) and not r[0]:
            return self.say(f"There was no shutdown or restart scheduled, {self.s}.")
        return self.say(f"Cancelled. Staying on, {self.s}." if ok else f"I couldn't cancel it, {self.s}. Quick, save your work.")

    def _a_theme_mode(self, x, ask):
        ok, _ = self._hand("theme_mode", x["mode"])
        return self.say(f"Windows {x['mode']} mode, {self.s}." if ok else f"I couldn't change Windows' theme, {self.s}.")

    # ---------- apps & sites ----------
    def _a_open_system(self, x, ask):
        target, label = SYSTEM[x["key"]]
        ok, r = self._hand("open_uri", target)
        return self.say(f"Opening {label}, {self.s}." if ok and r is not False else f"I couldn't open {label}, {self.s}.")

    def _a_open_site(self, x, ask):
        url = self.sites[x["key"]]
        self._hand("open_uri", url)
        name = x["key"].split("|")[0]
        nice = {"youtube": "YouTube", "github": "GitHub", "chatgpt": "ChatGPT", "tiktok": "TikTok", "twitter": "X"}.get(name, name.title())
        return self.say(f"{nice}, {self.s}.")

    def _a_open_domain(self, x, ask):
        url = x["url"]
        if not re.fullmatch(r"https://[a-z0-9.-]+/", url):
            return self.say(f"I didn't catch that address, {self.s}.")
        self._hand("open_uri", url)
        return self.say(f"Opening {url[8:-1]}, {self.s}.")

    # ---------- quick answers ----------
    def _a_coin(self, x, ask):
        return self.say(f"{random.choice(['Heads', 'Tails'])}, {self.s}.")

    def _a_dice(self, x, ask):
        n, sides = x["n"], x["sides"]
        rolls = [random.randint(1, sides) for _ in range(n)]
        if n == 1:
            return self.say(f"{rolls[0]}, {self.s}." + (" Natural twenty!" if sides == 20 and rolls[0] == 20 else ""))
        return self.say(f"{', '.join(map(str, rolls))}. That's {sum(rolls)}, {self.s}.")

    def _a_random_number(self, x, ask):
        return self.say(f"{random.randint(x['lo'], x['hi'])}, {self.s}.")

    def _a_days_until(self, x, ask):
        mo, d = HOLIDAYS[x["key"]]
        n = days_until(mo, d)
        name = x["key"].split("|")[0].title().replace("'S", "'s")
        return self.say(f"It's {name} today, {self.s}!" if n == 0 else f"{n} day{'s' if n != 1 else ''} until {name}, {self.s}.")

    def _a_math(self, x, ask):
        return self.say(f"{say_number(x['value'])}, {self.s}.")

    # ---------- Tab's progress ----------
    def _a_progress_show(self, x, ask):
        self._ui("show_progress")
        store = getattr(self.b, "progress", None)
        if store is None:
            return self.say(f"Opening Tab's progress, {self.s}.")
        return self.say(store.summary(self.s))

    # ---------- help ----------
    def _a_help_category(self, x, ask):
        cat = x["category"]
        items = dict(CATALOG)[cat]
        self._ui("workspace", True)
        self._ui("help_card", [cat.upper() + ": " + " · ".join(items)])
        return self.say(f"{cat}, {self.s}: " + "; ".join(items[:5]) + ". The rest are on screen.")
