"""Feeds the 3D brain view (core/brain3d + core/brainview, shared with JARVIS for Windows v5.6) with what JARVIS OS is
really doing. The brain window is its own process, so the daemon writes small JSON events (begin / end / tokens /
flash / note) to ~/.local/state/jarvis/brain_events.jsonl - but ONLY while a brain window is open (its pid file is
alive), so it costs nothing otherwise. The window replays them into its own BrainBus; Ollama /api/ps and nvidia-smi
(the loaded models and their VRAM) it reads itself. Inputs/outputs are redacted (core.brainbus.redact) before they are
written: memory and Tab text, codes, passwords, emails and phone-like numbers never reach the file."""
from __future__ import annotations
import functools, json, logging, os, threading, time

log = logging.getLogger("jarvisd.brainfeed")
STATE = os.path.join(os.path.expanduser("~"), ".local", "state", "jarvis")
EVENTS = "brain_events.jsonl"
MAX_BYTES = 256 * 1024

class Feed:
    def __init__(self, state=None, clock=time.time, is_open=None):
        self.state = state or STATE
        self.path = os.path.join(self.state, EVENTS)
        self.clock = clock
        self._open_cache = (0.0, False)
        self.is_open = is_open or self._window_open
        self.lock = threading.Lock()

    def _window_open(self):
        t, v = self._open_cache
        now = time.monotonic()
        if now - t < 2.0:
            return v
        from .workshop import window_pid
        v = window_pid("brain", self.state) is not None
        self._open_cache = (now, v)
        return v

    def emit(self, kind, key, **kw):
        if not key or not self.is_open():
            return False
        ev = {"t": self.clock(), "k": kind, "r": key}
        ev.update({k: v for k, v in kw.items() if v is not None})
        try:
            with self.lock:
                os.makedirs(self.state, exist_ok=True)
                if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev) + "\n")
            return True
        except OSError:
            return False

    def begin(self, key, model=None, detail="", source=None):
        return self.emit("begin", key, model=model, detail=detail, src=source)

    def end(self, key, to=None):
        return self.emit("end", key, to=to)

    def tokens(self, key, n=1):
        return self.emit("tok", key, n=n)

    def flash(self, key):
        return self.emit("flash", key)

    def note(self, key, direction, value):
        from .core import brainbus as bb
        try:
            t = bb.redact(key, bb.summarize(value))
        except Exception:
            t = ""
        return self.emit("note", key, d=direction, text=t) if t else False

FEED = Feed()

def replay(bus, lines):
    """Apply event lines (from the file) to a core.brainbus.BrainBus. Unknown regions and bad lines are skipped."""
    n = 0
    for line in lines:
        try:
            ev = json.loads(line)
        except Exception:
            continue
        k, r = ev.get("k"), ev.get("r")
        if r not in bus.r:
            continue
        if k == "begin":
            bus.begin(r, ev.get("model"), ev.get("detail") or "", ev.get("src"))
        elif k == "end":
            bus.end(r, to=ev.get("to"))
        elif k == "tok":
            bus.tokens(r, int(ev.get("n") or 1))
        elif k == "flash":
            bus.flash(r)
        elif k == "note":
            with bus.lock:
                bus.r[r].io.append((ev.get("t") or time.time(), ev.get("d") or "out", str(ev.get("text") or "")[:120]))
        else:
            continue
        n += 1
    return n

class Tail:
    """Reads the lines appended to the events file since the last call (starts at the end: no stale history)."""
    def __init__(self, path):
        self.path = path
        try:
            self.pos = os.path.getsize(path)
        except OSError:
            self.pos = 0

    def read(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.pos:          # rotated
            self.pos = 0
        if size == self.pos:
            return []
        with open(self.path, encoding="utf-8", errors="replace") as f:
            f.seek(self.pos); data = f.read(); self.pos = f.tell()
        return data.splitlines()

# ---------------------------------------------------------------- hooks on the real parts of JARVIS OS
def _wrap(cls, name, key, detail, feed, model_attr=None, source="core", to="core"):
    fn = getattr(cls, name, None)
    if fn is None or getattr(fn, "_brain_wrapped", False):
        return False
    @functools.wraps(fn)
    def w(self, *a, **k):
        m = getattr(self, model_attr, None) if model_attr else None
        first = a[0] if a else None
        if isinstance(first, str) and key != "safety":
            feed.note(key, "in", first)
        feed.begin(key, m if isinstance(m, str) else None, detail, source)
        try:
            res = fn(self, *a, **k)
        except Exception as e:
            feed.note(key, "out", "error: " + type(e).__name__); feed.end(key, to); raise
        feed.end(key, to)
        if key == "safety":
            feed.note(key, "out", "passed" if (res is True or (isinstance(res, tuple) and res and res[0])) else "blocked")
        elif isinstance(res, (str, tuple)):
            feed.note(key, "out", res if isinstance(res, str) else str(res[0]))
        return res
    w._brain_wrapped = True
    setattr(cls, name, w)
    return True

def install(feed=None):
    """Class-level, once. Never changes behaviour or return values. -> list of hooks that took."""
    feed = feed or FEED
    from .core import brainbus as bb
    done = []
    try:
        from . import race
        if not getattr(race.stream_ollama, "_brain_wrapped", False):
            orig = race.stream_ollama
            @functools.wraps(orig)
            def stream(base, model, messages, *a, **k):
                key = bb.region_for_model(model) or "race_a"
                feed.begin(key, model, "racing", "core")
                try:
                    for piece in orig(base, model, messages, *a, **k):
                        feed.tokens(key, 1)
                        yield piece
                finally:
                    feed.end(key, "voice")
            stream._brain_wrapped = True
            race.stream_ollama = stream
            done.append("race.stream_ollama")
        if not getattr(race.race, "_brain_wrapped", False):
            orig_race = race.race
            @functools.wraps(orig_race)
            def rc(*a, **k):
                res = orig_race(*a, **k)
                try:
                    if res is not None and res.winner:
                        feed.flash(bb.region_for_model(res.winner) or "race_a")
                except Exception:
                    pass
                return res
            rc._brain_wrapped = True
            race.race = rc
            done.append("race.race")
    except Exception as e:
        log.debug("brain hook race: %r", e)
    for modname, cls, meth, key, detail, mattr in (
            ("creator", "ImageMaker", "generate", "image", "painting", None),
            ("creator", "CodeMaker", "write", "coder", "writing code", "model"),
            ("safety", "SafetyGate", "is_safe", "safety", "checking a picture", None),
            ("voice_out", "Speaker", "speak", "voice", "speaking", None),
            ("brain", "LinuxBrain", "handle", "motor", "command", None)):
        try:
            mod = __import__("jarvisd." + modname, fromlist=[cls])
            if _wrap(getattr(mod, cls), meth, key, detail, feed, model_attr=mattr):
                done.append(f"{modname}.{cls}.{meth}")
        except Exception as e:
            log.debug("brain hook %s.%s: %r", cls, meth, e)
    return done
