"""Replies from Tab, spoken on the PC.

Tab posts each reply to a private ntfy.sh topic (a long random name nobody can guess).
The text is encrypted and signed with a key that only Tab and this PC hold, so the
ntfy server only ever sees scrambled bytes, and nobody else can make JARVIS talk.
JARVIS polls the topic every few seconds.

v3: a sealed message can also carry actions for this PC (see remote.py). Those are
only accepted when sealed with the same key, fresh (posted in the last few minutes)
and never seen before, so an old or copied message can't be replayed. Results go back
sealed the same way, to Tab's mailbox and to a second private topic.
Standard library only.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, logging, os, secrets, threading, time, urllib.parse, urllib.request

log = logging.getLogger("jarvis.replies")
MAGIC = "JRV1."

# ---------- sealing: HMAC-SHA256 counter-mode stream + encrypt-then-MAC ----------
def _keys(key_b64: str):
    k = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
    return (hmac.new(k, b"jarvis-enc", hashlib.sha256).digest(),
            hmac.new(k, b"jarvis-mac", hashlib.sha256).digest())

def _stream(ek, nonce, n):
    out = bytearray(); c = 0
    while len(out) < n:
        out += hmac.new(ek, nonce + c.to_bytes(8, "big"), hashlib.sha256).digest(); c += 1
    return bytes(out[:n])

def seal(obj: dict, key_b64: str) -> str:
    ek, mk = _keys(key_b64)
    pt = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(pt, _stream(ek, nonce, len(pt))))
    tag = hmac.new(mk, nonce + ct, hashlib.sha256).digest()
    return MAGIC + base64.urlsafe_b64encode(nonce + ct + tag).decode().rstrip("=")

def unseal(token: str, key_b64: str):
    """dict, or None if it isn't from Tab (wrong key, tampered, or junk)."""
    try:
        if not token or not token.startswith(MAGIC):
            return None
        raw = token[len(MAGIC):].strip()
        raw = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if len(raw) < 16 + 32:
            return None
        nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
        ek, mk = _keys(key_b64)
        if not hmac.compare_digest(tag, hmac.new(mk, nonce + ct, hashlib.sha256).digest()):
            return None
        pt = bytes(a ^ b for a, b in zip(ct, _stream(ek, nonce, len(ct))))
        return json.loads(pt.decode("utf-8"))
    except Exception:
        return None

MAGIC_BIN = b"JRB1"

def seal_bytes(data: bytes, key_b64: str) -> bytes:
    """Same sealing as seal(), for raw bytes (screenshots). JRB1 + nonce + ciphertext + tag."""
    ek, mk = _keys(key_b64)
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(ek, nonce, len(data))))
    tag = hmac.new(mk, b"bin" + nonce + ct, hashlib.sha256).digest()
    return MAGIC_BIN + nonce + ct + tag

def unseal_bytes(blob: bytes, key_b64: str):
    try:
        if not blob.startswith(MAGIC_BIN) or len(blob) < 4 + 16 + 32:
            return None
        nonce, ct, tag = blob[4:20], blob[20:-32], blob[-32:]
        ek, mk = _keys(key_b64)
        if not hmac.compare_digest(tag, hmac.new(mk, b"bin" + nonce + ct, hashlib.sha256).digest()):
            return None
        return bytes(a ^ b for a, b in zip(ct, _stream(ek, nonce, len(ct))))
    except Exception:
        return None

def result_topic(topic: str, key_b64: str) -> str:
    """The private topic results go back on: derived from the key, so only Tab and this PC know it."""
    k = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
    return "jr" + hmac.new(k, b"jarvis-result-topic:" + topic.encode(), hashlib.sha256).hexdigest()[:40]

# ---------- transport ----------
def fetch(server, topic, since, timeout=15):
    """New ntfy events after `since` (an event id, a unix time, or 'all')."""
    url = f"{server.rstrip('/')}/{urllib.parse.quote(topic)}/json?poll=1&since={urllib.parse.quote(str(since))}"
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-desktop/3.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    events = []
    for line in body.splitlines():
        line = line.strip()
        if line:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "message":
                events.append(ev)
    return events

def post(server, topic, token, timeout=15):
    req = urllib.request.Request(f"{server.rstrip('/')}/{urllib.parse.quote(topic)}", data=token.encode(),
                                 method="POST", headers={"User-Agent": "JARVIS-reply/3.0", "Cache": "yes"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def put_file(server, topic, blob: bytes, filename, message="", timeout=60):
    """Upload sealed bytes as an ntfy attachment (ntfy.sh keeps attachments ~3 h)."""
    req = urllib.request.Request(f"{server.rstrip('/')}/{urllib.parse.quote(topic)}", data=blob, method="PUT",
                                 headers={"User-Agent": "JARVIS-desktop/3.0", "Filename": filename,
                                          **({"Message": message} if message else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def normalize_activity(msg, key, fetch_image=None):
    """Sealed activity message -> {text, step, progress, image (jpeg/png bytes or None), ts, re}.
    The optional image is a sealed ntfy attachment (image_url + image_sha256), checked before use."""
    a = msg.get("activity") or {}
    out = {"text": str(a.get("text") or "")[:400], "step": str(a.get("step") or "")[:60], "ts": float(msg.get("ts") or time.time()),
           "re": msg.get("re"), "progress": None, "image": None}
    try:
        if a.get("progress") is not None:
            out["progress"] = max(0, min(100, int(float(a["progress"]))))
    except (TypeError, ValueError):
        pass
    # v5.1: optional task fields for the "Tab's progress" window (older PCs simply ignore them)
    if a.get("task"):
        out["task"] = str(a["task"])[:80]
        if a.get("task_id"): out["task_id"] = str(a["task_id"])[:60]
        st = str(a.get("state") or "").lower()
        if st in ("running", "queued", "paused", "done", "failed", "removed"): out["state"] = st
        if a.get("eta") is not None: out["eta"] = str(a["eta"])[:40]
        try:
            if a.get("percent") is not None: out["percent"] = max(0, min(100, int(float(a["percent"]))))
        except (TypeError, ValueError):
            pass
        if out.get("percent") is None and out["progress"] is not None: out["percent"] = out["progress"]
    url, sha = a.get("image_url"), a.get("image_sha256")
    if url and sha and str(url).startswith("https://"):
        try:
            blob = (fetch_image or _get_bytes)(url)
            data = unseal_bytes(blob, key)
            if data and hashlib.sha256(data).hexdigest() == sha and len(data) < 3_000_000:
                out["image"] = data
        except Exception as e:
            log.info("activity image: %r", e)
    return out

def _get_bytes(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-desktop/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(3_500_000)


class ReplyListener(threading.Thread):
    """Polls for Tab's replies; calls on_reply(text, meta) for each new one, oldest first."""
    def __init__(self, cfg, state_path, on_reply, is_paused=lambda: False):
        super().__init__(name="replies", daemon=True)
        self.cfg = cfg or {}
        self.server = self.cfg.get("server", "https://ntfy.sh")
        self.topic = self.cfg.get("topic", "")
        self.key = self.cfg.get("key", "")
        self.every = max(5.0, float(self.cfg.get("poll_seconds", 8)))
        self.max_age = float(self.cfg.get("max_age_minutes", 120)) * 60
        self.action_ttl = float(self.cfg.get("action_max_age_seconds", 900))
        self.state_path = state_path
        self.on_reply = on_reply
        self.on_activity = None           # v5: on_activity(dict) for "Tab's screen"
        self.is_paused = is_paused
        self.stop_ev = threading.Event()
        self.wake_ev = threading.Event()
        self.boost_until = 0.0
        self.seen = []
        self.since = None
        self.last_ok = None
        try:
            st = json.load(open(state_path, encoding="utf-8"))
            self.since = st.get("since"); self.seen = st.get("seen", [])[-400:]
        except Exception:
            pass
        if not self.since:
            # first run: only replies from a few minutes before now
            self.since = str(int(time.time()) - 60)

    def _save(self):
        try:
            json.dump({"since": self.since, "seen": self.seen[-400:]}, open(self.state_path, "w", encoding="utf-8"))
        except OSError:
            pass

    def boost(self, seconds=120):
        """Tab is about to answer: check every 4 s for a while (ntfy.sh allows bursts)."""
        self.boost_until = time.time() + seconds
        self.wake_ev.set()

    def poll_once(self):
        events = fetch(self.server, self.topic, self.since)
        out = []
        for ev in events:
            self.since = ev.get("id") or self.since
            msg = unseal(ev.get("message", ""), self.key)
            if msg and msg.get("kind") == "activity":
                # v5 "Tab's screen": what Tab is doing right now. Never spoken, never acted on.
                # (Older clients ignore these: there's no text/say/actions at the top level.)
                mid = msg.get("mid") or ev.get("id")
                if mid in self.seen:
                    continue
                self.seen.append(mid)
                sent = float(msg.get("ts") or ev.get("time") or time.time())
                if time.time() - sent > float(self.cfg.get("activity_max_age_seconds", 600)):
                    continue
                if str(msg.get("re") or "").lower().startswith("jtest"):
                    continue
                if self.on_activity:
                    try:
                        self.on_activity(normalize_activity(msg, self.key))
                    except Exception:
                        log.exception("activity handler failed")
                continue
            if not msg or not (msg.get("text") or msg.get("actions") is not None or msg.get("say")):
                log.info("ignored a message on the reply topic that isn't sealed with our key")
                continue
            # v3 safety net: old "jtest" smoke replies must never speak or act on his PC.
            # Unscoped speech stays allowed for proactive replies (birthday, deliveries).
            re_id = str(msg.get("re") or "").lower()
            if re_id.startswith("jtest"):
                log.warning("ignored a test message from the reply channel")
                continue
            mid = msg.get("mid") or ev.get("id")
            if mid in self.seen:
                continue
            self.seen.append(mid)
            sent = float(msg.get("ts") or 0)
            posted = float(ev.get("time") or time.time())
            age = time.time() - (sent or posted)
            msg["stale"] = age > self.max_age
            if msg.get("actions") is not None:
                # actions: must be fresh. ts is set by Tab right before posting; ev.time is the
                # ntfy server's clock. A replayed copy has an old ts but a new ev.time.
                ttl = min(3600.0, max(60.0, float(msg.get("ttl") or self.action_ttl)))
                if not sent or abs(posted - sent) > 300:
                    msg["rejected"] = "replayed or delayed (sent %.0fs before ntfy got it)" % (posted - sent if sent else -1)
                elif time.time() - posted > ttl:
                    msg["rejected"] = "expired: arrived %.0f min ago, while JARVIS was off" % ((time.time() - posted) / 60)
            out.append(msg)
        if events:
            self._save()
        self.last_ok = time.time()
        return out

    def run(self):
        if not (self.topic and self.key):
            log.warning("reply channel not configured; Tab's replies will only reach the phone")
            return
        log.info("listening for Tab's replies every %.0fs", self.every)
        backoff = self.every
        while not self.stop_ev.is_set():
            try:
                for msg in self.poll_once():
                    try:
                        self.on_reply(msg.get("text") or "", msg)
                    except Exception as e:
                        log.error("reply handler: %r", e)
                backoff = self.every
            except Exception as e:
                backoff = min(backoff * 2, 120)
                log.info("reply poll failed (%r); next try in %.0fs", e, backoff)
            # poll faster for a couple of minutes after a command went to Tab (answer expected)
            wait = min(backoff, 4.0) if time.time() < self.boost_until and backoff == self.every else backoff
            self.wake_ev.wait(wait); self.wake_ev.clear()
