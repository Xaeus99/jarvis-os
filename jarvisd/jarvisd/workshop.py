"""The hologram workshop, the vitals HUD, the update countdown and the 3D brain view for JARVIS OS (shared design
with JARVIS for Windows v5.6; the 3D code is vendored in core/: holo, holoview, brain3d, brainbus, brainview, hud56).

parse(text) -> (action, extra) or None;  Workshop(brain).handle(action, extra) -> what JARVIS says.
  "show me a 3D model of Earth" / "visualize a rocket" / "design a new suit"  -> the hologram window
  while a hologram is open: "make it purple and gold", "add wings", "show the skeleton", "spin faster", "zoom in" ...
  "save this model" (JSON + PNG + OBJ for Blender), "close the hologram"
  "show vitals" (CPU / RAM / GPU / VRAM / temperatures / network, live)
  "when's the next update" (the countdown; Tab sets it with the signed update_eta action, or `jarvisctl eta set`)
  "show your brain" / "thought cloud" (the 3D brain; click a region to see what that part is doing)

Every window is its own process (python -m jarvisd.holowin KIND), like the viewer: a crash there never touches JARVIS.
The daemon talks to the hologram window through ~/.local/state/jarvis/holo.json: it holds a RECIPE (a preset name +
parameters, or the free-form JSON spec), never a built scene, and the window re-validates it before drawing.
Safety, same as Windows: the picture blocklists (adult, gore, hate, real people) run on every request and on every
free-form design's title and labels; the models are built from glowing primitives (never photoreal), the human model
is a wooden-mannequin figure, there are no logos, and nothing opens during a game."""
from __future__ import annotations
import datetime, json, logging, os, re, subprocess, sys, threading, time

log = logging.getLogger("jarvisd.workshop")

STATE = os.path.join(os.path.expanduser("~"), ".local", "state", "jarvis")
HOLO_FILE = "holo.json"
ETA_FILE = "update_eta.json"
PY = "/usr/lib/jarvis-os/venv/bin/python"

F = lambda rx, t: re.fullmatch(rx, t)
THREE_D = r"(?:3d|3 d|3-d|three d|three dimensional|holographic|hologram|holo)"
SHOW = (r"(?:show(?: me)?|make(?: me)?|build(?: me)?|create(?: me)?|render(?: me)?|display|give me|project|open|pull up|"
        r"bring up|generate(?: me)?|draw(?: me)?)")

def _clean(text):
    t = (text or "").strip(" .!?,").lower()
    t = re.sub(r"\s+", " ", t)
    return re.sub(r"^(?:hey |ok |okay )?(?:jarvis,? )?(?:can you |could you |would you |please )*", "", t)

def parse(text):
    """-> (action, extra) or None. Same phrases as Windows v5.6."""
    t = _clean(text)
    m = (F(SHOW + r" (?:a |an |the |me a |me an )?" + THREE_D + r" (?:model|version|view|image|picture|render|hologram|design)? ?(?:of )?(?:a |an |the )?(.+)", t)
         or F(r"(?:a |an )?" + THREE_D + r" (?:model|hologram|render) of (?:a |an |the )?(.+)", t)
         or F(r"(?:hologram|holo) (?:of )?(?:a |an |the )?(.+)", t)
         or F(r"visuali[sz]e (?:a |an |the |me a |me an )?(.+?)(?: in 3d| for me)?", t)
         or F(SHOW + r" (?:a |an |the )?(.+?) (?:in 3d|in three d|as a hologram|in hologram|holographically)", t))
    if m:
        what = m.group(1).strip(" .")
        if what and not re.fullmatch(r"(?:that|it|this|my screen|the screen|workshop|lab|viewer)", what):
            mm = re.search(re.escape(what), re.sub(r"\s+", " ", text or ""), re.I)   # the words as said (capitals kept)
            return "holo_show", {"what": what, "raw": mm.group(0) if mm else what}
    m = F(r"(?:design|build|make|create)(?: me)? (?:a |an |my )?(?:new |custom )?(?:iron ?man |armou?red |power )?(?:suit|armou?r)(?: of armou?r)?(?: (.+))?", t)
    if m:
        return "holo_show", {"what": "suit" + (" " + m.group(1) if m.group(1) else "")}
    if F(r"(?:open |show |start )?(?:the |my )?hologram (?:workshop|lab|viewer)|(?:open |show )(?:the |my )?(?:holo|3d) (?:workshop|lab)", t):
        return "holo_show", {"what": ""}
    if F(r"(?:save|export|keep) (?:this|that|the|my) (?:3d |hologram |holo )?(?:model|hologram|design|suit)(?: (?:for blender|as obj))?", t):
        return "holo_save", {}
    if F(r"(?:close|hide|exit|dismiss) (?:the |my )?(?:hologram|holo|3d model|model viewer|hologram workshop|workshop)", t):
        return "holo_close", {}
    if F(r"(?:show|open|display|bring up|pull up)(?: me)? (?:my |the |system |pc |computer )*(?:vitals|vital signs|system monitor|performance monitor|stats window)|"
         r"(?:system|pc|computer) vitals|vitals|how(?:'s| is) my (?:pc|computer) (?:doing|holding up)", t):
        return "vitals", {}
    if F(r"(?:close|hide) (?:the |my )?(?:vitals|system vitals|system monitor)", t):
        return "vitals_close", {}
    if F(r"(?:when(?:'s| is| will) (?:the )?next update(?: (?:be )?(?:ready|done|coming|here))?|update (?:timer|countdown|eta|clock)|"
         r"(?:show|open)(?: me)? (?:the )?(?:update|next update) (?:timer|countdown|eta|clock)|"
         r"how (?:long|much longer) (?:until|till|for|is) (?:the )?(?:next )?update(?: going to (?:be|take))?|next update (?:timer|countdown|eta))", t):
        return "eta_show", {}
    if F(r"(?:close|hide) (?:the )?(?:update )?(?:timer|countdown)", t):
        return "eta_hide", {}
    if F(r"(?:show|open|display)(?: me)? (?:your|the|jarvis'?s?) (?:brain|mind|thought cloud|neural (?:net|network)|neurons)|"
         r"(?:open |show )?(?:the )?thought cloud|show (?:me )?what you(?:'re| are) thinking|brain view", t):
        return "brain", {}
    if F(r"(?:close|hide) (?:your |the )?(?:brain|thought cloud)", t):
        return "brain_close", {}
    return None

SUIT_EDITS = [
    (r"(?:make it|paint it|change it to|go|turn it) (\w+)(?: and (\w+))?(?: colou?rs?)?", "colors"),
    (r"(?:bigger|larger|wider|beefier|huge) shoulders", "shoulders_up"),
    (r"(?:smaller|narrower|slimmer) shoulders", "shoulders_down"),
    (r"(?:add|give it|put on|with) (?:some )?wings", "wings_on"),
    (r"(?:remove|take off|no|lose the|drop the) wings", "wings_off"),
    (r"(?:make it )?(?:a )?stealth(?: version| mode| suit)?|go stealth", "stealth_on"),
    (r"(?:normal|regular|default|standard) (?:version|suit|colou?rs)|(?:turn off|no more) stealth", "stealth_off"),
]
VIEW_EDITS = [
    (r"(?:spin|rotate|turn) (?:it )?faster", "faster"), (r"(?:spin|rotate|turn) (?:it )?slower", "slower"),
    (r"(?:stop|pause) (?:spinning|rotating|the spin|the rotation)|hold (?:it )?still", "stop"),
    (r"(?:start )?(?:spin|rotate)(?:ning)?(?: it)?(?: again)?", "spin"),
    (r"zoom in(?: more)?|closer", "zoom_in"), (r"zoom out(?: more)?|further|farther", "zoom_out"),
    (r"reset (?:the )?(?:view|camera)", "reset"),
]

# ---------------------------------------------------------------- safety
_PEOPLE = re.compile(r"\b(?:my|our|his|her) (?:friend|mom|mum|dad|brother|sister|teacher|girlfriend|boyfriend|crush|classmate|"
                     r"cousin|aunt|uncle|grandma|grandpa|neighbou?r|boss|coach)\b|\bdeep ?fake|face ?swap|\bselfie\b|\bmy face\b")

def check_text(text, raw=None):
    """The picture blocklists (core.holo.check_text -> jarvisd.safety.check_prompt, fail-closed) plus a stricter
    real-person rule for holograms: someone you know, or a first + last name that isn't a known fictional character."""
    from .core import holo
    ok, kind = holo.check_text(text)
    if not ok:
        return ok, kind
    t = (text or "").lower()
    if _PEOPLE.search(t):
        return False, "real_person"
    if re.fullmatch(r"(?:a |an |the )?(?:me|myself|my (?:own )?(?:face|body|head))", t.strip()):
        return False, "real_person"
    try:   # the whole subject is a first + last name ("Jake Miller") that isn't a known fictional character
        from . import safety
        src = (raw if raw is not None else (text or "")).strip(" .!?,")
        m = re.fullmatch(r"(?:[Aa]n? |[Tt]he )?([A-Z][a-z]+(?: [A-Z][a-z']+){1,2})", src)
        if m and safety.FICTIONAL.sub(" ", m.group(1).lower()).strip() == m.group(1).lower() and \
                not re.match(r"(?:jarvis|iron|new|los|san|las|solar|planet|space|death|millennium|saturn|mars|the)\b", m.group(1).lower()):
            return False, "real_person"
    except Exception:
        return False, "checks_unavailable"
    return True, None

# ---------------------------------------------------------------- update countdown
def clean_eta(a):
    """{version, percent, eta_iso, step} (the signed update_eta action, or `jarvisctl eta set`) -> a safe dict or None."""
    if not isinstance(a, dict):
        return None
    ver = re.sub(r"[^0-9A-Za-z.\-]", "", str(a.get("version") or ""))[:12]
    try:
        pct = max(0, min(100, int(float(a.get("percent") or 0))))
    except Exception:
        return None
    from .core.hud56 import parse_eta
    iso = str(a.get("eta_iso") or "")[:40]
    if not ver or parse_eta(iso) is None:
        return None
    step = re.sub(r"[^\w\s.,:;'()/+\-]", "", str(a.get("step") or ""))[:80].strip()
    return {"version": ver, "percent": pct, "eta_iso": iso, "step": step, "set_at": time.time()}

def read_json(name, state=None):
    try:
        with open(os.path.join(state or STATE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def write_json(name, d, state=None):
    state = state or STATE
    os.makedirs(state, exist_ok=True)
    p = os.path.join(state, name); tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, p)
    return p

def set_eta(d, state=None):
    c = clean_eta(d)
    if c is None:
        return None
    write_json(ETA_FILE, c, state)
    return c

def get_eta(state=None):
    return read_json(ETA_FILE, state) or None

def eta_line(eta, sir="sir", now=None):
    from .core.hud56 import countdown_text
    if not eta:
        return f"No update is on the way right now, {sir}. When Tab starts one, the countdown appears here."
    txt, left = countdown_text(eta, now=now)
    v, pct = eta.get("version"), eta.get("percent")
    if left is None:
        return f"Version {v} is {pct} percent built, {sir}."
    if left <= 0:
        return f"Version {v} is finishing up, {sir}. {pct} percent built."
    mins = max(1, int(round(left / 60)))
    step = f" Right now: {eta['step']}." if eta.get("step") else ""
    return f"Version {v} is {pct} percent built, {sir}, about {mins} minute{'s' if mins != 1 else ''} to go.{step}"

# ---------------------------------------------------------------- windows (own processes)
def window_pid(kind, state=None):
    """The pid of an open JARVIS window of this kind (holo / vitals / countdown / brain), or None."""
    try:
        pid = int(open(os.path.join(state or STATE, f"win_{kind}.pid")).read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None

def open_window(kind, run=subprocess.run):
    argv = [PY, "-m", "jarvisd.holowin", kind]
    unit = f"jarvis-{kind}-{int(time.time() * 1000) % 10 ** 9}"
    try:
        r = run(["systemd-run", "--user", "--collect", f"--unit={unit}", "-p", "WorkingDirectory=/usr/lib/jarvis-os",
                 "-E", "QT_QPA_PLATFORM=xcb"] + argv, capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError) as e:
        log.info("systemd-run %s: %r", kind, e)
    try:
        env = dict(os.environ); env.setdefault("QT_QPA_PLATFORM", "xcb")
        subprocess.Popen([sys.executable] + argv[1:], env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return True
    except OSError as e:
        log.warning("%s window: %r", kind, e); return False

def close_window(kind, state=None):
    import signal
    pid = window_pid(kind, state)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM); return True
        except OSError:
            pass
    return False

# ---------------------------------------------------------------- recipes -> scenes
def recipe_for(what, last=None):
    """'earth with the moon' / 'a new suit in purple' / 'human skeleton' -> a preset recipe, or None (free-form)."""
    from .core import holo
    w = (what or "").lower()
    pre = holo.closest_preset(w) if w else ((last or {}).get("preset") or "earth")
    if pre == "earth":
        return {"preset": "earth", "params": {"moon": bool(re.search(r"\bmoon\b", w))}}
    if pre == "suit" and not re.search(r"\b(?:car|motorcycle|bike|truck)\b", w):
        prm = dict(holo.SUIT_DEFAULT)
        cols = re.findall(r"\b(" + "|".join(holo.COLORS) + r")\b", w)
        if cols:
            prm["primary"] = cols[0]
            if len(cols) > 1:
                prm["secondary"] = cols[1]
        if re.search(r"\bwings?\b", w): prm["wings"] = True
        if re.search(r"\bstealth\b", w): prm["stealth"] = True
        if re.search(r"(?:big|huge|wide) shoulders", w): prm["shoulders"] = 1.35
        return {"preset": "suit", "params": prm}
    if pre == "mannequin":
        return {"preset": "mannequin", "params": dict(skeleton=bool(re.search(r"skeleton|bones?", w)),
                organs=bool(re.search(r"organs?|heart|lungs?", w)), muscles=bool(re.search(r"muscles?", w)))}
    if pre in ("solar", "dna", "atom", "rocket") or (pre == "car" and re.fullmatch(r"(?:a |an |the )?(?:car|sports car|race ?car)", w)):
        return {"preset": pre, "params": {}}
    return None

def scene_from_recipe(r):
    """The window and the tests build scenes the same way. A free-form spec is validated again here."""
    from .core import holo
    if not isinstance(r, dict):
        return None
    if r.get("preset"):
        if r["preset"] not in holo.PRESETS:
            return None
        prm = {k: v for k, v in (r.get("params") or {}).items() if k in ("moon", "primary", "secondary", "shoulders", "wings",
                                                                       "stealth", "skeleton", "organs", "muscles")}
        return holo.build(r["preset"], **prm)
    if r.get("spec") is not None:
        sc, _ = holo.validate_spec(r["spec"])
        if sc is None:
            return None
        ok, _ = check_text(" ".join(str(p.get("label") or "") for p in sc["parts"]) + " " + str(sc.get("title", "")))
        return sc if ok else None
    return None

def ask_coder(what, cfg=None, opener=None, timeout=300):
    """The local coder model (qwen2.5-coder, the one "write code" uses) answers with a JSON scene. -> text or None."""
    from . import localai, creator
    import urllib.request
    c = localai.cfg_of(cfg)
    op = opener or urllib.request.urlopen
    if not localai.has_model(creator.CODER, c["url"], op):
        return None
    from .core import holo
    body = {"model": creator.CODER, "stream": False, "keep_alive": "3m", "format": "json",
            "options": {"temperature": 0.3, "num_ctx": 4096, "num_predict": 2500},
            "messages": [{"role": "system", "content": holo.SPEC_SYSTEM}, {"role": "user", "content": "Design: " + what[:200]}]}
    with localai._req(c["url"] + "/api/chat", body, timeout=timeout, opener=op) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return (d.get("message") or {}).get("content") or None


class Workshop:
    def __init__(self, brain, state=None, launch=open_window, close=close_window, coder=ask_coder, threaded=True):
        self.b = brain
        self.state = state or getattr(brain, "state_dir", None) or STATE
        self.launch, self.close, self.coder, self.threaded = launch, close, coder, threaded
        self.last = None          # the current recipe (+ title)
        self.seq = 0

    @property
    def sir(self):
        return getattr(self.b, "sir", "sir")

    def say(self, text):
        return self.b.say(text)

    def gaming(self):
        try:
            return bool(self.b.is_gaming())
        except Exception:
            return False

    def holo_open(self):
        return window_pid("holo", self.state) is not None

    def _publish(self, recipe=None, view=None, save=None):
        d = read_json(HOLO_FILE, self.state)
        self.seq = max(self.seq, int(d.get("seq") or 0)) + 1
        d["seq"] = self.seq
        if recipe is not None:
            d["recipe"] = recipe; d.pop("view", None); d["keep_camera"] = bool(recipe.get("keep_camera"))
        if view is not None:
            d["view"] = {"seq": self.seq, "what": view}
        if save is not None:
            d["save"] = {"seq": self.seq, "folder": save}
        write_json(HOLO_FILE, d, self.state)
        return d

    def show(self, recipe, keep_camera=False):
        r = dict(recipe); r["keep_camera"] = keep_camera
        self.last = r
        self._publish(recipe=r)
        if not self.holo_open():
            self.launch("holo")

    # ------------------------------------------------------------------
    def handle(self, action, extra=None):
        extra = extra or {}
        s = self.sir
        if action == "holo_close":
            self.close("holo", self.state); return self.say(f"Hologram off, {s}.")
        if action == "holo_save":
            if not self.last:
                return self.say(f"There's no hologram to save yet, {s}. Say show me a 3D model of something.")
            from .creator import user_dir
            folder = os.path.join(user_dir("documents"), "JARVIS Models")
            self._publish(save=folder)
            if not self.holo_open():
                self.launch("holo")
            return self.say(f"Saving it to Documents, JARVIS Models, {s}: the model, a picture, and an OBJ file for Blender.")
        if action in ("vitals", "eta_show", "brain") and self.gaming():
            return self.say(f"I'll keep that closed while you're in a game, {s}.")
        if action == "vitals":
            if window_pid("vitals", self.state) is None:
                self.launch("vitals")
            return self.say(f"Vitals up, {s}.")
        if action == "vitals_close":
            self.close("vitals", self.state); return self.say(f"Vitals closed, {s}.")
        if action == "eta_show":
            eta = get_eta(self.state)
            if eta and window_pid("countdown", self.state) is None:
                self.launch("countdown")
            return self.say(eta_line(eta, s))
        if action == "eta_hide":
            self.close("countdown", self.state); return self.say(f"Countdown hidden, {s}.")
        if action == "brain":
            if window_pid("brain", self.state) is None:
                self.launch("brain")
            return self.say(f"Here's my thought cloud, {s}. Click a region to see what that part of me is doing.")
        if action == "brain_close":
            self.close("brain", self.state); return self.say(f"Brain view closed, {s}.")
        if action == "holo_show":
            return self._holo_show(extra.get("what", ""), extra.get("raw"))
        return None

    def _decline(self, kind):
        s = self.sir
        if kind == "real_person":
            return self.say(f"I don't model real people, {s}. I can do a mannequin, or an original character.")
        if kind == "gore":
            return self.say(f"That's not something I'll build, {s}. Shall I do a suit of armour or a spaceship instead?")
        if kind == "checks_unavailable":
            return self.say(f"My safety checks didn't load, {s}, so I won't build anything just now.")
        return self.say(f"I'll pass on that one, {s}. Try Earth, a new suit, a rocket, or anything else you like.")

    def _holo_show(self, what, raw=None):
        s = self.sir
        if self.gaming():
            return self.say(f"Holograms stay closed during games, {s}. Ask me again after the match.")
        ok, kind = check_text(what, raw)
        if not ok:
            return self._decline(kind)
        r = recipe_for(what, self.last)
        if r is not None:
            self.show(r)
            title = (scene_from_recipe(r) or {}).get("title", "your model")
            extra = {"suit": " Try make it purple and gold, bigger shoulders, add wings, or stealth version.",
                     "mannequin": " Say show the skeleton, organs or muscles.",
                     "earth": " Say show the moon too."}.get(r["preset"], "")
            return self.say(f"{title}, {s}.{extra}")
        self.say(f"Designing {what[:60]}, {s}. Give me a moment.")
        if self.threaded:
            threading.Thread(target=self._design, args=(what,), daemon=True, name="holo-design").start()
            return ""
        return self._design(what)

    def _design(self, what):
        """Free-form: the local coder model writes the JSON, validated strictly; else the closest preset."""
        from .core import holo
        s = self.sir; note = ""
        r = None
        try:
            txt = self.coder(what, getattr(self.b, "cfg", None))
            if txt is None:
                note = "nomodel"
            else:
                cand = {"spec": txt}
                if scene_from_recipe(cand) is not None:
                    r = cand
        except Exception as e:
            log.info("holo design: %r", e)
        if r is None:
            pre = holo.closest_preset(what)
            if pre:
                r = recipe_for(pre) or {"preset": pre, "params": {}}
                note = note or "closest"
        if r is None:
            return self.say(f"I couldn't design that one, {s}. Try describing it with simpler shapes, or ask for Earth, a suit or a rocket.")
        if self.gaming():
            return self.say(f"Your design is ready, {s}; I'll keep it closed until the game's over. Say show the hologram workshop then.")
        self.show(r)
        sc = scene_from_recipe(r) or {}
        title = sc.get("title", "your model")
        if note == "closest":
            return self.say(f"I couldn't design that exactly, {s}, so here's the closest thing I have: {title}.")
        if note == "nomodel":
            return self.say(f"Free-form designs need the coding model, {s}; say write me some code once to download it. Here's {title} meanwhile.")
        return self.say(f"Designed: {title}, {s}. {len(sc.get('parts', []))} parts. Drag to orbit, scroll to zoom.")

    def edit(self, raw):
        """While the hologram window is open: voice edits. -> spoken text, or None when it isn't an edit."""
        from .core import holo
        if not self.last or not self.holo_open():
            return None
        t = re.sub(r"^(?:can you |could you |please |jarvis,? |now )+", "", _clean(raw))
        s = self.sir
        pre = self.last.get("preset"); prm = dict(self.last.get("params") or {})
        new = None; msg = ""
        if pre == "suit":
            for rx, what in SUIT_EDITS:
                m = re.fullmatch(rx, t)
                if not m:
                    continue
                if what == "colors":
                    c1, c2 = m.group(1), m.group(2)
                    if c1 not in holo.COLORS or (c2 and c2 not in holo.COLORS):
                        return None
                    prm["primary"] = c1; prm["stealth"] = False
                    if c2:
                        prm["secondary"] = c2
                elif what == "shoulders_up":
                    prm["shoulders"] = min(1.8, float(prm.get("shoulders", 1.0)) + 0.25)
                elif what == "shoulders_down":
                    prm["shoulders"] = max(0.6, float(prm.get("shoulders", 1.0)) - 0.25)
                elif what in ("wings_on", "wings_off"):
                    prm["wings"] = what == "wings_on"
                elif what in ("stealth_on", "stealth_off"):
                    prm["stealth"] = what == "stealth_on"
                new = {"preset": "suit", "params": prm}
                msg = {"colors": f"Repainted, {s}.", "shoulders_up": f"Shoulders up-armoured, {s}.", "shoulders_down": f"Slimmer shoulders, {s}.",
                       "wings_on": f"Wings deployed, {s}.", "wings_off": f"Wings stowed, {s}.", "stealth_on": f"Stealth plating, {s}.",
                       "stealth_off": f"Back to standard plating, {s}."}[what]
                break
        if new is None and pre == "earth":
            if re.fullmatch(r"(?:show|add|bring in|include) (?:me )?(?:the )?moon(?: too| as well)?|(?:and|with) the moon(?: too)?", t):
                new = {"preset": "earth", "params": {"moon": True}}; msg = f"The Moon, {s}. To scale it's a bit further out."
            elif re.fullmatch(r"(?:hide|remove|lose) (?:the )?moon", t):
                new = {"preset": "earth", "params": {"moon": False}}; msg = f"Moon removed, {s}."
        if new is None and pre == "mannequin":
            m = re.fullmatch(r"(show|hide|add|remove|turn on|turn off)(?: me)? (?:the )?(skeleton|bones|organs|muscles)", t)
            if m:
                layer = {"bones": "skeleton"}.get(m.group(2), m.group(2))
                prm[layer] = m.group(1) in ("show", "add", "turn on")
                new = {"preset": "mannequin", "params": {k: bool(prm.get(k)) for k in ("skeleton", "organs", "muscles")}}
                msg = f"{layer.capitalize()} {'on' if prm[layer] else 'off'}, {s}."
        if new is not None:
            self.show(new, keep_camera=True)
            return self.say(msg)
        for rx, what in VIEW_EDITS:
            if re.fullmatch(rx, t):
                self._publish(view=what)
                return self.say({"faster": f"Faster, {s}.", "slower": f"Slower, {s}.", "stop": f"Holding still, {s}.",
                                 "spin": f"Spinning, {s}.", "zoom_in": f"Closer, {s}.", "zoom_out": f"Pulling back, {s}.",
                                 "reset": f"View reset, {s}."}[what])
        return None
