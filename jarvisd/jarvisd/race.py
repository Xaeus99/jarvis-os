"""The local AI race: several free models on this PC answer at once and the first good sentence wins.

Each model streams its answer from Ollama on localhost; the first one to produce a good first sentence wins, the
others are cancelled, and every sentence is spoken the moment it is complete, so JARVIS starts talking before the
answer is finished. Shared with JARVIS for Windows (v5.2 llm.py); stdlib only, nothing leaves the PC.
"""
from __future__ import annotations
import http.client, json, logging, queue, re, threading, time, urllib.parse

log = logging.getLogger("jarvisd.race")

HANDOFF = "HANDOFF"     # a model that answers only this word hands the request on (unused by the Linux persona)

# ---------------------------------------------------------------- text -> speech pieces
_MD = [(re.compile(r"```.*?```", re.S), " "), (re.compile(r"`([^`]*)`"), r"\1"), (re.compile(r"\*\*?([^*]+)\*\*?"), r"\1"),
       (re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+", re.M), ""), (re.compile(r"^#+\s*", re.M), ""),
       (re.compile(r"https?://\S+"), ""), (re.compile(r"[*_#>|~]+"), ""),
       (re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"), "")]

def clean(text):
    t = text or ""
    for rx, rep in _MD:
        t = rx.sub(rep, t)
    return re.sub(r"\s+", " ", t).strip()

class SentenceSplitter:
    """Feed streamed deltas; get back whole sentences to speak as early as possible.
    The very first piece may be cut at a comma once it has a few words, so speech starts sooner."""
    END = re.compile(r"(.+?[.!?\u2026]+[\"')\]]*)(?:\s+|$)", re.S)
    def __init__(self, first_clause_words=7, max_chars=220):
        self.buf = ""; self.n = 0
        self.first_clause_words = first_clause_words; self.max_chars = max_chars
    def feed(self, delta):
        self.buf += delta or ""
        out = []
        while True:
            m = self.END.match(self.buf)
            # a full stop needs the next character to know it really ended ("3.5", "Mr.")
            if m and (m.end() < len(self.buf) or self.buf.endswith((" ", "\n"))):
                s = m.group(1)
                if re.search(r"\b(?:Mr|Mrs|Ms|Dr|St|vs|etc|e\.g|i\.e)\.$", s) or re.search(r"\d\.$", s) and re.match(r"\d", self.buf[m.end():] or "x"):
                    break
                out.append(s); self.buf = self.buf[m.end():]
                continue
            break
        if not out and self.n == 0:
            m = re.match(r"(.+?[,;:\u2014])\s+", self.buf)
            if m and len(m.group(1).split()) >= self.first_clause_words:
                out.append(m.group(1)); self.buf = self.buf[m.end():]
        if not out and len(self.buf) > self.max_chars:
            cut = self.buf.rfind(" ", 0, self.max_chars)
            if cut > 40:
                out.append(self.buf[:cut]); self.buf = self.buf[cut + 1:]
        res = [c for c in (clean(s) for s in out) if c]
        self.n += len(res)
        return res
    def flush(self):
        s = clean(self.buf); self.buf = ""
        if s:
            self.n += 1
            return [s]
        return []

def is_handoff(text):
    t = (text or "").strip().strip(".!\"' ").upper()
    return t.startswith(HANDOFF) or t in ("HAND OFF", "HAND-OFF")

def maybe_handoff_prefix(text):
    t = (text or "").lstrip().upper()
    return bool(t) and (HANDOFF.startswith(t[:len(HANDOFF)]) or t.startswith(HANDOFF))

def is_good(sentence):
    s = (sentence or "").strip()
    if len(s) < 2 or not re.search(r"[A-Za-z0-9]", s):
        return False
    if is_handoff(s):
        return False
    return True

# ---------------------------------------------------------------- streaming clients (stdlib only)
class Cancelled(Exception):
    pass

def _open_stream(url, body, headers, timeout):
    u = urllib.parse.urlsplit(url)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=timeout)
    path = u.path + ("?" + u.query if u.query else "")
    conn.request("POST", path, body=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    resp = conn.getresponse()
    if resp.status != 200:
        detail = resp.read(400).decode("utf-8", "replace")
        conn.close()
        raise RuntimeError(f"HTTP {resp.status}: {detail[:200]}")
    return conn, resp

def stream_ollama(base, model, messages, cancel=None, options=None, keep_alive="30m", timeout=60):
    """Yields text deltas from Ollama's /api/chat (NDJSON stream)."""
    body = {"model": model, "messages": messages, "stream": True, "keep_alive": keep_alive,
            "options": {"temperature": 0.6, "num_predict": 180, "num_ctx": 2048, **(options or {})}}
    conn, resp = _open_stream(base.rstrip("/") + "/api/chat", body, {}, timeout)
    try:
        for line in resp:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("error"):
                raise RuntimeError(d["error"])
            piece = (d.get("message") or {}).get("content") or ""
            if piece:
                yield piece
            if d.get("done"):
                break
    finally:
        conn.close()

# ---------------------------------------------------------------- the race
class Candidate:
    def __init__(self, name, tier, gen_fn):
        self.name, self.tier, self.gen_fn = name, tier, gen_fn     # gen_fn(cancel_event) -> iterator of deltas

class RaceResult:
    def __init__(self):
        self.winner = None; self.tier = None; self.handoff = False; self.first_s = None
        self.text = ""; self.errors = {}; self.timed_out = False; self.sentences = []
    @property
    def ok(self):
        return bool(self.winner) and not self.handoff and bool(self.text)
    def __repr__(self):
        return f"RaceResult(winner={self.winner}, tier={self.tier}, handoff={self.handoff}, first={self.first_s}, errors={self.errors}, text={self.text[:60]!r})"

def race(candidates, on_sentence, decide_timeout=6.0, total_timeout=45.0, clock=time.monotonic, splitter=SentenceSplitter):
    """Run every candidate at once. The first to produce a good first sentence wins (its sentences go to on_sentence
    as they complete); the first to say HANDOFF before anyone wins makes the whole race a handoff. Losers are cancelled."""
    res = RaceResult()
    if not candidates:
        res.errors["none"] = "no models available"; return res
    q = queue.Queue()
    cancels = {c.name: threading.Event() for c in candidates}
    t0 = clock()
    # The handoff probe holds back the first ~12 characters; re-feed them when they turn out not to be HANDOFF.
    def runner(c):
        sp = splitter(); head = ""; released = False
        try:
            for delta in c.gen_fn(cancels[c.name]):
                if cancels[c.name].is_set():
                    return
                if not released:
                    head += delta
                    if maybe_handoff_prefix(head) and len(head.strip()) < len(HANDOFF) + 2:
                        if is_handoff(head) and len(head.strip()) >= len(HANDOFF):
                            q.put(("handoff", c, None)); return
                        continue
                    if is_handoff(head):
                        q.put(("handoff", c, None)); return
                    released = True
                    pieces = sp.feed(head)
                else:
                    pieces = sp.feed(delta)
                for s in pieces:
                    q.put(("sentence", c, s))
            if not released and is_handoff(head):
                q.put(("handoff", c, None)); return
            if not released:
                sp.feed(head)
            for s in sp.flush():
                q.put(("sentence", c, s))
            q.put(("done", c, None))
        except Cancelled:
            return
        except Exception as e:
            q.put(("error", c, repr(e)[:200]))
    threads = [threading.Thread(target=runner, args=(c,), daemon=True, name="race-" + c.name) for c in candidates]
    for th in threads:
        th.start()
    alive = {c.name for c in candidates}
    winner = None
    while alive:
        limit = (decide_timeout if winner is None else total_timeout) - (clock() - t0)
        if limit <= 0:
            res.timed_out = winner is None
            break
        try:
            kind, c, val = q.get(timeout=min(limit, 0.5))
        except queue.Empty:
            continue
        if winner is not None and c.name != winner.name:
            continue
        if kind == "error":
            res.errors[c.name] = val; alive.discard(c.name)
            if winner is not None:
                break
            continue
        if kind == "done":
            alive.discard(c.name)
            if winner is not None:
                break
            continue
        if kind == "handoff":
            if winner is None:
                res.handoff = True; res.winner = c.name; res.tier = c.tier
                res.first_s = round(clock() - t0, 3)
                break
            continue
        # sentence
        if winner is None:
            if not is_good(val):
                continue
            winner = c; res.winner = c.name; res.tier = c.tier; res.first_s = round(clock() - t0, 3)
            for other in candidates:
                if other.name != c.name:
                    cancels[other.name].set()
        res.sentences.append(val)
        try:
            on_sentence(val)
        except Exception:
            log.exception("on_sentence failed")
    for ev in cancels.values():
        ev.set()
    res.text = " ".join(res.sentences).strip()
    return res
