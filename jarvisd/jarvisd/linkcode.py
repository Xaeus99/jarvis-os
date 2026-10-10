"""Link JARVIS OS to Tab with a short one-time code, when the Windows drive can't be read (BitLocker without the
recovery key at hand, or Windows already gone).

  Tab side:   tools/make_link_code.py SETTINGS.json   -> prints a code like  7KQ2-M9XD-R4TB  (good for 1 hour)
  This PC:    jarvisctl link --code 7KQ2-M9XD-R4TB

The code is never sent anywhere. Both sides derive two things from it: a ntfy topic name and a sealing key
(PBKDF2-SHA256, 200k rounds). Tab posts the link settings sealed with that key (core/replies.py: encrypt + MAC) to
that topic; this PC fetches the one message, checks the seal and the expiry, and copies only the link settings.
ntfy only ever sees ciphertext on a random-looking topic. 12 characters of Crockford base32 = 60 bits.
"""
from __future__ import annotations
import base64, hashlib, json, secrets, time

from .core import replies

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"          # Crockford base32: no I, L, O, U
SERVER = "https://ntfy.sh"
TTL_S = 3600

def new_code():
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))

def normalize(code):
    """Forgiving about case, spaces, dashes and look-alikes (O->0, I/L->1). None if it can't be a code."""
    c = (code or "").upper().replace("O", "0").replace("I", "1").replace("L", "1")
    c = "".join(ch for ch in c if ch.isalnum())
    if len(c) != 12 or any(ch not in ALPHABET for ch in c):
        return None
    return c

def derive(code):
    """(topic, key_b64) for a code."""
    c = normalize(code)
    if not c:
        raise ValueError("not a JARVIS link code")
    topic = "jarvis-link-" + hashlib.sha256(("topic:" + c).encode()).hexdigest()[:40]
    k = hashlib.pbkdf2_hmac("sha256", c.encode(), b"jarvis-os-link-v1", 200_000, 32)
    return topic, base64.urlsafe_b64encode(k).decode().rstrip("=")

LINK_SECTIONS = ("forwarding", "replies", "assistant", "briefing")

def bundle(settings, now=None):
    """Only the link-relevant sections of a Windows-JARVIS-shaped settings dict, with an expiry."""
    b = {k: settings[k] for k in LINK_SECTIONS if isinstance(settings.get(k), dict)}
    b["v"] = 1
    b["exp"] = int((now or time.time()) + TTL_S)
    return b

def publish(code, settings, post=replies.post, server=SERVER, now=None):
    topic, key = derive(code)
    post(server, topic, replies.seal(bundle(settings, now), key))
    return topic

def fetch(code, get=replies.fetch, server=SERVER, now=None):
    """The newest unexpired bundle sealed for this code, or None."""
    topic, key = derive(code)
    best = None
    for ev in get(server, topic, "all") or []:
        m = replies.unseal(ev.get("message", ""), key)
        if isinstance(m, dict) and m.get("v") == 1 and m.get("exp", 0) >= (now or time.time()):
            if best is None or m["exp"] > best["exp"]:
                best = m
    return best
