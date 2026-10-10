"""v5.5 thought cloud: the activity bus. Every part of JARVIS reports here when it loads, works or finishes; the brain
window (brainview.py) only draws what this bus says. Pure Python, thread-safe, no Qt, nothing leaves the PC.

Regions (anatomy name, what it really is in JARVIS):
  PREFRONTAL      the thinker (deepseek-r1)                   HIPPOCAMPUS   memory + config (what he told JARVIS to remember)
  CORTEX α/β/γ    the three race models (winner flashes)      AMYGDALA      the safety checks (NSFW, gore, blocklists)
  OCCIPITAL       the eyes (Moondream)                        MOTOR CORTEX  PC actions (apps, windows, lights, OBS)
  AUDITORY        the ears (whisper)                          CEREBELLUM    the coder (qwen2.5-coder)
  BROCA           the voice (Kokoro / Piper)                  VISUAL IMAG.  the image maker (SD-Turbo)
  THALAMUS        the Tab link (mail relay)
"""
from __future__ import annotations
import collections, contextlib, functools, json, logging, math, re, threading, time, urllib.request

log = logging.getLogger("jarvis.brainbus")

# key, label, role, default model, parameters (shown as the neuron count), colour (r,g,b), angle (deg), radius (0..1)
REGIONS = [
    ("thinker",  "PREFRONTAL",       "Thinker",        "deepseek-r1",       1.78e9, (200, 120, 255),  -90, 0.90),
    ("race_a",   "CORTEX \u03b1",    "Race",           "llama3.2:1b",       1.24e9, (90, 170, 255),  -138, 0.74),
    ("race_b",   "CORTEX \u03b2",    "Race",           "llama3.2:3b",       3.21e9, (70, 210, 240),   -42, 0.74),
    ("race_c",   "CORTEX \u03b3",    "Race",           "qwen2.5:3b",        3.09e9, (120, 140, 255),  -90, 0.45),
    ("eyes",     "OCCIPITAL",        "Eyes",           "moondream",         1.86e9, (80, 230, 160),    90, 0.88),
    ("ears",     "AUDITORY",         "Ears",           "whisper",           0.39e9, (240, 200, 90),   160, 0.95),
    ("voice",    "BROCA",            "Voice",          "Kokoro",            0.08e9, (255, 150, 90),  -168, 0.96),
    ("image",    "VISUAL IMAGINATION", "Image maker",  "SD-Turbo",          1.30e9, (255, 110, 200),   28, 0.96),
    ("coder",    "CEREBELLUM",       "Coder",          "qwen2.5-coder:3b",  3.09e9, (130, 255, 120),  125, 0.86),
    ("tab",      "THALAMUS",         "Tab link",       "mail relay",        0,      (120, 200, 255),   -8, 0.97),
    ("memory",   "HIPPOCAMPUS",      "Memory",         "config + memory",   0,      (170, 140, 255),  145, 0.48),
    ("safety",   "AMYGDALA",         "Safety",         "NSFW + gore",       0,      (255, 90, 110),    52, 0.50),
    ("motor",    "MOTOR CORTEX",     "PC actions",     "commands",          0,      (255, 220, 120),  -12, 0.50),
]
KEYS = [r[0] for r in REGIONS]
SPEC = {r[0]: r for r in REGIONS}

def neurons_for(params):
    """Dots drawn per region: more parameters, more neurons (log scale, 14..48)."""
    if not params:
        return 18
    return int(max(18, min(60, 11 * math.log10(params / 1e7))))

def neuron_label(params):
    """'1.2B neurons' for a model (its parameter count); 'logic' for the parts that are plain code."""
    if not params:
        return "logic"
    return (f"{params / 1e9:.1f}B" if params >= 1e9 else f"{params / 1e6:.0f}M") + " neurons"

def region_for_model(name):
    """Map a model or candidate name to its region (None if it isn't one of JARVIS's local models)."""
    n = (name or "").lower()
    if not n:
        return None
    if "deepseek" in n or "-r1" in n:
        return "thinker"
    if "coder" in n:
        return "coder"
    if "moondream" in n or "llava" in n:
        return "eyes"
    if "whisper" in n:
        return "ears"
    if "kokoro" in n or "piper" in n:
        return "voice"
    if "sd-turbo" in n or "stable" in n or "sd_turbo" in n:
        return "image"
    if re.search(r"llama3\.2:1b|llama.*\b1b\b", n):
        return "race_a"
    if re.search(r"llama3\.2:3b|llama.*\b3b\b|llama3\.2(?::latest)?$", n):
        return "race_b"
    if "qwen" in n:
        return "race_c"
    return None


class Region:
    __slots__ = ("key", "model", "busy", "level", "loaded", "vram", "tok_s", "tokens", "fired", "flash_until",
                 "last_active", "detail", "_tok_t0", "_tok_n", "io")

    def __init__(self, key):
        self.key = key; self.model = SPEC[key][3]; self.busy = 0; self.level = 0.0; self.loaded = False
        self.vram = None; self.tok_s = None; self.tokens = 0; self.fired = 0; self.flash_until = 0.0
        self.last_active = 0.0; self.detail = ""; self._tok_t0 = None; self._tok_n = 0
        self.io = collections.deque(maxlen=IO_KEEP)     # v5.6: last few (wall time, "in"/"out", short redacted text)



# ---------------------------------------------------------------- v5.6: what each region saw and said (short, redacted)
IO_KEEP = 6
_SENSITIVE = re.compile(r"\b(?:pass ?words?|passcodes?|pins?|codes?|otp|2fa|secret|private|token|api ?key|keys?|login|"
                        r"address|phone|e-?mail|card|bank|ssn|social security|birthday|born|lives? (?:at|on|in)|school)\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DIGITS = re.compile(r"\d[\d\s-]{5,}\d")    # phone-like runs (7+), not "2026" or "384,400"
_AFTER_SECRET = re.compile(r"\b(pass ?word|passcode|pin|code|key)( is| =|:)?\s+\S+", re.I)
PRIVATE_REGIONS = ("memory", "tab")

def redact(key, text, limit=90):
    """Short, safe text for the region panel. Emails and long numbers are always masked, a secret after 'password is'
    too; the memory and Tab link regions hide the whole line when it touches anything personal."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = re.sub(r"<think>.*?(?:</think>|$)", "", t, flags=re.S | re.I).strip()
    if not t:
        return ""
    if key in PRIVATE_REGIONS and _SENSITIVE.search(t):
        return "[private \u2014 hidden]"
    t = _EMAIL.sub("[email]", t)
    t = _AFTER_SECRET.sub(lambda m: m.group(1) + " [hidden]", t)
    t = _DIGITS.sub("[number]", t)
    if key in PRIVATE_REGIONS:
        limit = min(limit, 60)
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "\u2026"

def summarize(x):
    """A value going into or out of a part -> a few words (never the bytes)."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, bool):
        return "ok" if x else "blocked"
    if isinstance(x, (bytes, bytearray)):
        return f"{len(x) // 1024} KB of data"
    raw = getattr(x, "raw", None)
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(x, (tuple, list)) and x:
        if isinstance(x[0], bool):
            return ("ok" if x[0] else "blocked") + (f" ({x[1]})" if len(x) > 1 and isinstance(x[1], str) and x[1] else "")
        return summarize(x[0])
    if isinstance(x, dict):
        for k in ("text", "answer", "reply", "path", "prompt"):
            if isinstance(x.get(k), str):
                return x[k]
        return "result"
    size = getattr(x, "size", None)
    if isinstance(size, tuple) and len(size) == 2:
        return f"picture {size[0]}x{size[1]}"
    n = type(x).__name__
    if n in ("ndarray",):
        return "audio" if getattr(x, "ndim", 1) == 1 else "frame"
    if hasattr(x, "__fspath__"):
        import os as _os
        return _os.path.basename(_os.fspath(x))
    return n


class BrainBus:
    """begin/end mark work; tokens() feeds the tokens/s meter; flash() marks a race winner; fire(a, b) sends a
    streak from one region to another. snapshot() is what the window draws."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.r = {k: Region(k) for k in KEYS}
        self.events = []          # (t, kind, a, b): fire/flash, newest last, trimmed
        self.listeners = []       # callables(kind, region) for the "pop up while thinking" hook
        self.vram_total = None; self.vram_used = None; self.gpu_name = None
        self.cpu_pct = None; self.ram_pct = None   # v5.6: the HUD ring around the core
        self.update_eta = None                     # v5.6: the next-update countdown (shown in the header)
        self.state = "idle"       # core state: idle / listening / thinking / speaking

    def _ev(self, kind, a, b=None):
        self.events.append((self.clock(), kind, a, b))
        if len(self.events) > 200:
            del self.events[:100]

    def _notify(self, kind, key):
        for fn in list(self.listeners):
            try:
                fn(kind, key)
            except Exception:
                log.debug("brain listener failed", exc_info=True)

    def note(self, key, direction, value):
        """v5.6: remember a short, redacted line of what went into (direction 'in') or came out of ('out') a part."""
        if key not in self.r:
            return
        try:
            t = redact(key, summarize(value))
        except Exception:
            t = ""
        if not t:
            return
        with self.lock:
            self.r[key].io.append((time.time(), direction, t))

    def region_info(self, key):
        """v5.6: everything the side panel shows for one region (a copy; safe from any thread)."""
        if key not in self.r:
            return None
        sp = SPEC[key]
        now = self.clock()
        with self.lock:
            g = self.r[key]
            return {"key": key, "label": sp[1], "role": sp[2], "model": g.model, "busy": g.busy > 0,
                    "loaded": g.loaded, "vram": g.vram, "vram_text": fmt_bytes(g.vram) or "\u2014",
                    "tok_s": g.tok_s, "tokens": g.tokens, "fired": g.fired, "detail": g.detail,
                    "status": stat_line(g, now),
                    "idle_for": (now - g.last_active) if g.last_active else None,
                    "io": list(g.io)}

    def begin(self, key, model=None, detail="", source=None):
        if key not in self.r:
            return
        with self.lock:
            g = self.r[key]; g.busy += 1; g.fired += 1; g.last_active = self.clock(); g.loaded = True
            if model: g.model = model
            g.detail = detail or g.detail
            g._tok_t0 = None; g._tok_n = 0
            self._ev("fire", source or "core", key)
        self._notify("begin", key)

    def end(self, key, ok=True, to=None):
        if key not in self.r:
            return
        with self.lock:
            g = self.r[key]; g.busy = max(0, g.busy - 1); g.last_active = self.clock()
            if g.busy == 0:
                g.detail = ""
            self._ev("fire", key, to or "core")
        self._notify("end", key)

    @contextlib.contextmanager
    def active(self, key, model=None, detail="", source=None, to=None):
        self.begin(key, model, detail, source)
        try:
            yield self
        finally:
            self.end(key, to=to)

    def tokens(self, key, n=1):
        if key not in self.r:
            return
        now = self.clock()
        with self.lock:
            g = self.r[key]; g.tokens += n; g.last_active = now
            if g._tok_t0 is None:
                g._tok_t0 = now; g._tok_n = 0
            g._tok_n += n
            dt = now - g._tok_t0
            if dt >= 0.25:
                g.tok_s = round(g._tok_n / dt, 1)

    def flash(self, key, seconds=1.6):
        if key not in self.r:
            return
        with self.lock:
            self.r[key].flash_until = self.clock() + seconds
            self._ev("flash", key)
        self._notify("flash", key)

    def fire(self, a, b):
        with self.lock:
            self._ev("fire", a, b)

    def set_state(self, s):
        self.state = s or "idle"
        if s in ("thinking",):
            self._notify("think", "core")

    def set_loaded(self, key, loaded=True, vram=None, model=None):
        if key not in self.r:
            return
        with self.lock:
            g = self.r[key]; g.loaded = loaded; g.vram = vram if loaded else None
            if model: g.model = model

    def apply_ollama_ps(self, models):
        """models: Ollama /api/ps "models" list -> which regions are loaded and their VRAM."""
        seen = {}
        for m in models or []:
            k = region_for_model(m.get("name") or m.get("model"))
            if k:
                seen[k] = (m.get("size_vram") or 0, m.get("name") or m.get("model"))
        with self.lock:
            for k in ("thinker", "race_a", "race_b", "race_c", "eyes", "coder"):
                g = self.r[k]
                if k in seen:
                    g.loaded = True; g.vram = seen[k][0]; g.model = seen[k][1]
                elif g.busy == 0:
                    g.loaded = False; g.vram = None
        return seen

    def snapshot(self, levels=None, ease=0.25):
        """[(key, label, role, model, params, colour, angle, radius, dict(live stats))], plus core info.
        v5.6: levels={} eases into that dict instead (the replay recorder samples without disturbing the window)."""
        now = self.clock()
        out = []
        with self.lock:
            for k in KEYS:
                s = SPEC[k]; g = self.r[k]
                idle_for = now - g.last_active if g.last_active else 1e9
                target = 1.0 if g.busy else (0.55 if idle_for < 2.5 else (0.28 if g.loaded else 0.08))
                if levels is None:
                    g.level += (target - g.level) * ease; lv = g.level
                else:
                    lv = levels.get(k, 0.08); lv += (target - lv) * ease; levels[k] = lv
                if not g.busy and idle_for > 3 and levels is None:
                    g.tok_s = None
                out.append(dict(key=k, label=s[1], role=s[2], model=g.model, params=s[4], color=s[5], angle=s[6],
                                radius=s[7], busy=g.busy > 0, loaded=g.loaded, level=round(lv, 3),
                                vram=g.vram, tok_s=g.tok_s, fired=g.fired, flashing=now < g.flash_until,
                                detail=g.detail, stat=stat_line(g, now)))
            ev = [e for e in self.events if now - e[0] < 2.5]
        return dict(regions=out, events=ev, state=self.state, vram_used=self.vram_used, vram_total=self.vram_total,
                    gpu=self.gpu_name, now=now, cpu=self.cpu_pct, ram=self.ram_pct)

    def firing_rate(self):
        """Fraction of regions working right now (the core's 'firing' number)."""
        with self.lock:
            return round(sum(1 for g in self.r.values() if g.busy) / len(self.r), 2)


def fmt_bytes(n):
    if not n:
        return None
    return f"{n / 2**30:.1f} GB" if n >= 2**30 * 0.95 else f"{n / 2**20:.0f} MB"

def stat_line(g, now):
    bits = []
    if g.busy:
        bits.append(f"{g.tok_s:.0f} tok/s" if g.tok_s else (g.detail or "working"))
    elif g.loaded:
        bits.append("loaded")
    else:
        bits.append("idle")
    v = fmt_bytes(g.vram)
    if v:
        bits.append(v)
    return " \u00b7 ".join(bits)


BUS = BrainBus()

# ---------------------------------------------------------------- hooks
def wrap_method(cls, name, key, model_attr=None, detail="", source=None, to=None, bus=None):
    """Patch cls.name so every call lights region `key`. Idempotent; never changes behaviour or return values."""
    fn = getattr(cls, name, None)
    if fn is None or getattr(fn, "_brain_wrapped", False):
        return False
    bus = bus or BUS

    @functools.wraps(fn)
    def w(self, *a, **k):
        m = None
        if model_attr:
            m = getattr(self, model_attr, None)
            m = m if isinstance(m, str) else None
        kk = key(self, *a, **k) if callable(key) else key
        try:
            first = a[0] if a else next(iter(k.values()), None)
            if kk != "safety" and isinstance(first, str) or hasattr(first, "raw"):
                bus.note(kk, "in", first)
            elif kk == "safety":
                bus.note(kk, "in", "checking a picture" if not isinstance(first, str) else "checking a prompt")
        except Exception:
            pass
        with bus.active(kk, m, detail, source, to):
            try:
                res = fn(self, *a, **k)
            except Exception as e:
                bus.note(kk, "out", "error: " + type(e).__name__)
                raise
        try:
            bus.note(kk, "out", res)
        except Exception:
            pass
        return res
    w._brain_wrapped = True
    setattr(cls, name, w)
    return True

def _thinker_key(self, question, gaming=False, model=None, on_cpu=False):
    return "thinker"

def install_hooks(bus=None):
    """Wire the real parts of JARVIS to the bus (class-level, once). Returns the list of hooks that took."""
    bus = bus or BUS
    done = []
    def hook(modname, cls, meth, key, **kw):
        try:
            mod = __import__("jarvis." + modname, fromlist=[cls])
            if wrap_method(getattr(mod, cls), meth, key, bus=bus, **kw):
                done.append(f"{modname}.{cls}.{meth}")
        except Exception as e:
            log.debug("brain hook %s.%s.%s: %r", modname, cls, meth, e)
    hook("thinker", "Thinker", "solve", "thinker", detail="reasoning")
    hook("vision", "Eyes", "ask_model", "eyes", model_attr="model", detail="looking")
    hook("listener", "Ears", "transcribe", "ears", detail="transcribing", to="core")
    hook("tts_kokoro", "KokoroSpeaker", "synth", "voice", model_attr="name", detail="speaking", source="core")
    hook("tts_piper", "PiperSpeaker", "render", "voice", detail="speaking", source="core")
    hook("creator", "ImageMaker", "generate", "image", detail="painting")
    hook("creator", "CodeMaker", "_ask", "coder", model_attr="model", detail="writing code")
    hook("forward", "Forwarder", "send", "tab", detail="sending to Tab")
    hook("forward", "Forwarder", "send_screen", "tab", detail="sending to Tab")
    for c in ("NsfwCheck", "GoreCheck", "SafetyGate"):
        hook("safety", c, "is_safe", "safety", detail="checking", source="image")
    hook("brain", "Brain", "_handle", "motor", detail="command")
    try:   # the voice region also glows for as long as a sentence is actually playing
        from . import voice as _v
        V = _v.Voice
        if not getattr(V._mark_start, "_brain_wrapped", False):
            ms, fo = V._mark_start, V._finished_one
            def mark_start(self, text):
                self._brain_playing = getattr(self, "_brain_playing", 0) + 1
                bus.begin("voice", detail="speaking", source="core")
                return ms(self, text)
            def finished_one(self):
                if getattr(self, "_brain_playing", 0) > 0:
                    self._brain_playing -= 1
                    bus.end("voice", to="core")
                return fo(self)
            mark_start._brain_wrapped = finished_one._brain_wrapped = True
            V._mark_start, V._finished_one = mark_start, finished_one
            done.append("voice.Voice.play")
    except Exception as e:
        log.debug("brain hook voice: %r", e)
    try:
        from . import safety as _s
        if not getattr(_s.check_prompt, "_brain_wrapped", False):
            orig = _s.check_prompt
            @functools.wraps(orig)
            def cp(*a, **k):
                bus.begin("safety", detail="blocklist"); 
                try:
                    return orig(*a, **k)
                finally:
                    bus.end("safety", to="image")
            cp._brain_wrapped = True
            _s.check_prompt = cp
            done.append("safety.check_prompt")
    except Exception:
        pass
    return done

# ---------------------------------------------------------------- live VRAM / loaded models
def _get_json(url, timeout=1.5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def nvidia_smi(run=None):
    """(used_bytes, total_bytes, name) from nvidia-smi, or None. Hidden window, 2 s timeout."""
    import subprocess, sys
    run = run or subprocess.run
    try:
        kw = dict(capture_output=True, text=True, timeout=2)
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000
        p = run(["nvidia-smi", "--query-gpu=memory.used,memory.total,name", "--format=csv,noheader,nounits"], **kw)
        line = (p.stdout or "").strip().splitlines()[0]
        used, total, name = [x.strip() for x in line.split(",", 2)]
        return int(float(used)) * 2**20, int(float(total)) * 2**20, name
    except Exception:
        return None

class Poller:
    """Every `every` seconds while the brain window is open: Ollama /api/ps (which models sit on the GPU and how
    much VRAM each uses) and nvidia-smi totals. Stops when the window closes, so it costs nothing in the background."""

    def __init__(self, bus=None, url="http://127.0.0.1:11434", every=2.0, get=_get_json, smi=nvidia_smi):
        self.bus = bus or BUS; self.url = url; self.every = every; self.get = get; self.smi = smi
        self._stop = threading.Event(); self.th = None

    def once(self):
        try:
            d = self.get(self.url.rstrip("/") + "/api/ps")
            self.bus.apply_ollama_ps(d.get("models") or [])
        except Exception:
            pass
        s = self.smi()
        if s:
            self.bus.vram_used, self.bus.vram_total, self.bus.gpu_name = s
        try:                                   # v5.6: CPU and RAM for the HUD ring around the core
            import psutil
            self.bus.cpu_pct = psutil.cpu_percent(None); self.bus.ram_pct = psutil.virtual_memory().percent
        except Exception:
            pass

    def start(self):
        if self.th and self.th.is_alive():
            return
        self._stop.clear()
        def loop():
            while not self._stop.is_set():
                self.once()
                self._stop.wait(self.every)
        self.th = threading.Thread(target=loop, name="brain-poll", daemon=True); self.th.start()

    def stop(self):
        self._stop.set()
