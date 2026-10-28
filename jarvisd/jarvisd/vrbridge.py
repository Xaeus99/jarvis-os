r"""JARVIS VR bridge on JARVIS OS: the Quest app (https://jarvis-vr.sites.tab.bot) talks to JARVIS on this PC.

The same protocol as Windows JARVIS 7.1 (jarvis/vr71.py there), so one headset app pairs with either system.

Why it works the way it does (the mixed-content problem)
- The VR app has to be an https page: WebXR (immersive-vr / immersive-ar) and the headset microphone only run in a secure
  context, and Quest Browser only installs https web apps to the library. An https page may NOT open ws:// to the LAN.
- So JARVIS serves its WebSocket over TLS (wss://<this PC's LAN IP>:8771) with a self-signed certificate it makes on
  first use (~/.local/share/jarvis/vr/cert.pem, ECDSA P-256, 10 years, the LAN IP as its name). Once per headset, the
  app opens https://<PC IP>:8771/ in a tab, he taps "Advanced -> Proceed" on the browser's warning, the page says
  "Trusted", and from then on the app's wss connection is accepted. LAN only: it binds to the private IP, refuses anyone
  not on a private address, and every connection must prove it holds a paired device secret. The image's firewall opens
  8771 to home-network (private) addresses only (firewalld: the jarvis-vr service in the home zone, and in the
  default desktop zone a rule that refuses 8771 from anything outside the jarvis-lan ipset).
- Fallback: the ntfy.sh relay over https, so it works when the trust step is skipped or blocked. Everything on the relay
  is sealed (JVR1: encrypt-then-MAC with keys from the pairing secret); audio rides as sealed attachments. ntfy.sh's free
  tier allows about 250 messages a day per home IP, so on the relay JARVIS batches spoken lines and sends PC stats only
  when asked.

Pairing ("Jarvis, pair my headset")
- JARVIS shows and says a 6-digit code (valid 5 minutes, one use, 5 wrong tries and it's dead) plus its LAN address.
- The headset makes an ephemeral P-256 key pair, and sends its public key with HMAC(code_key, pub) (code_key = PBKDF2 of
  the code). JARVIS checks the HMAC, makes its own ephemeral key, and returns a random 32-byte device secret encrypted
  under the ECDH shared secret, with HMAC(code_key, transcript). Someone watching the relay learns nothing (no ECDH key),
  and an active guesser gets 5 tries in 5 minutes. The same exchange runs over wss or the relay.
- After pairing: wss connections answer a fresh challenge with HMAC(device secret, nonce); relay messages are sealed
  with keys derived from the device secret on topics derived from it. "Unpair my headset" deletes every device.

What the headset may do: it is treated like his own microphone (push-to-talk goes through the same brain and the same
confirm flows), plus: PC stats, now playing + play/pause/next/previous (MPRIS), and the Hub shortcuts.
Standard library only, except the certificate (the cryptography package, in the image's JARVIS venv).
"""
from __future__ import annotations
import base64, hashlib, hmac, ipaddress, json, logging, os, secrets, select, socket, ssl, struct, threading, time, urllib.parse, urllib.request

log = logging.getLogger("jarvisd.vrbridge")

PORT = 8771
APP_URL = "https://jarvis-vr.sites.tab.bot/"
ORIGINS = ("https://jarvis-vr.sites.tab.bot",)
CODE_TTL = 300
IDLE_S = 120                      # a LAN session with nothing from the headset for this long is closed
CODE_TRIES = 5
PAIR_SALT = b"jarvis-vr-pair-v1"
PAIR_ROUNDS = 100_000
MAGIC = "JVR1."
BMAGIC = b"JVB1"
AMAGIC = b"JVA1"
A_PCM16, A_ULAW, A_WAV = 1, 2, 3
RATE = 16000
MAX_UTTER_S = 30
MAX_AGE_S = 600
RELAY_SERVERS = ("https://ntfy.sh",)
RELAY_DAILY_BUDGET = 200
MAX_ATTACH = 2_000_000
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def b64(b): return base64.b64encode(b).decode()
def unb64(s): return base64.b64decode(s + "=" * (-len(s) % 4))
def _h(key, label): return hmac.new(key, label, hashlib.sha256)

# ------------------------------------------------------------------ P-256 ECDH (stdlib; cryptography used when present)
_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_A = _P - 3
_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
_G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
      0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)

def _jadd(p, q):
    if p is None: return q
    if q is None: return p
    X1, Y1, Z1 = p; X2, Y2, Z2 = q
    Z1Z1 = Z1 * Z1 % _P; Z2Z2 = Z2 * Z2 % _P
    U1 = X1 * Z2Z2 % _P; U2 = X2 * Z1Z1 % _P
    S1 = Y1 * Z2 * Z2Z2 % _P; S2 = Y2 * Z1 * Z1Z1 % _P
    if U1 == U2:
        return _jdbl(p) if S1 == S2 else None
    H = (U2 - U1) % _P; R = (S2 - S1) % _P
    H2 = H * H % _P; H3 = H * H2 % _P; U1H2 = U1 * H2 % _P
    X3 = (R * R - H3 - 2 * U1H2) % _P
    Y3 = (R * (U1H2 - X3) - S1 * H3) % _P
    return (X3, Y3, H * Z1 * Z2 % _P)

def _jdbl(p):
    if p is None: return None
    X, Y, Z = p
    if Y == 0: return None
    YY = Y * Y % _P; S = 4 * X * YY % _P; ZZ = Z * Z % _P
    M = (3 * (X - ZZ) * (X + ZZ)) % _P
    X3 = (M * M - 2 * S) % _P
    return (X3, (M * (S - X3) - 8 * YY * YY) % _P, 2 * Y * Z % _P)

def _mul(k, pt):
    r = None; q = (pt[0], pt[1], 1)
    while k:
        if k & 1: r = _jadd(r, q)
        q = _jdbl(q); k >>= 1
    if r is None: raise ValueError("point at infinity")
    zi = pow(r[2], -1, _P); zi2 = zi * zi % _P
    return (r[0] * zi2 % _P, r[1] * zi2 * zi % _P)

def _on_curve(x, y):
    return 0 <= x < _P and 0 <= y < _P and (y * y - (x * x * x + _A * x + _B)) % _P == 0

def ec_keypair():
    """-> (private int, 65-byte uncompressed public point), the format WebCrypto's exportKey("raw") gives."""
    d = secrets.randbelow(_N - 1) + 1
    x, y = _mul(d, _G)
    return d, b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")

def ec_shared(d, pub):
    """-> the 32-byte x coordinate (what WebCrypto deriveBits(ECDH, 256) returns). Refuses points off the curve."""
    if len(pub) != 65 or pub[0] != 4:
        raise ValueError("bad public key")
    x, y = int.from_bytes(pub[1:33], "big"), int.from_bytes(pub[33:], "big")
    if not _on_curve(x, y):
        raise ValueError("point not on P-256")
    return _mul(d, (x, y))[0].to_bytes(32, "big")

# ------------------------------------------------------------------ pairing crypto
def code_key(code):
    c = "".join(ch for ch in str(code) if ch.isdigit())
    if len(c) != 6:
        raise ValueError("not a 6-digit code")
    return hashlib.pbkdf2_hmac("sha256", c.encode(), PAIR_SALT, PAIR_ROUNDS, 32)

def pair_topics(ck):
    """Relay topics for one pairing code: (headset->PC, PC->headset)."""
    return ("jvp" + _h(ck, b"jarvis-vr-pair-up").hexdigest()[:40], "jvq" + _h(ck, b"jarvis-vr-pair-down").hexdigest()[:40])

def pair_request_mac(ck, pub):
    return _h(ck, b"jvr-pair-req" + pub).digest()

def pair_answer(ck, head_pub, secret, dev_id, keypair=None):
    """PC side, after checking the request MAC. -> the 'paired' message dict."""
    d, pc_pub = keypair or ec_keypair()
    shared = ec_shared(d, head_pub)
    k = _h(shared, b"jvr-pair-k" + head_pub + pc_pub).digest()
    pad = _h(k, b"jvr-pair-enc").digest()
    ct = bytes(a ^ b for a, b in zip(secret, pad))
    tag = _h(ck, b"jvr-pair-ok" + head_pub + pc_pub + ct + dev_id.encode()).digest()
    return {"t": "paired", "dev": dev_id, "pub": b64(pc_pub), "ct": b64(ct), "tag": b64(tag)}

def pair_finish(ck, head_d, head_pub, msg):
    """Headset side (mirrors vrapp/site/js/jvr-crypto.js; used by the tests). -> (dev_id, secret) or None."""
    pc_pub, ct, tag, dev = unb64(msg["pub"]), unb64(msg["ct"]), unb64(msg["tag"]), str(msg["dev"])
    want = _h(ck, b"jvr-pair-ok" + head_pub + pc_pub + ct + dev.encode()).digest()
    if not hmac.compare_digest(want, tag):
        return None
    shared = ec_shared(head_d, pc_pub)
    k = _h(shared, b"jvr-pair-k" + head_pub + pc_pub).digest()
    pad = _h(k, b"jvr-pair-enc").digest()
    return dev, bytes(a ^ b for a, b in zip(ct, pad))

def auth_mac(secret, nonce):
    return _h(secret, b"jvr-auth" + nonce.encode()).hexdigest()

def device_topics(secret):
    return ("jv" + _h(secret, b"jarvis-vr-cmd").hexdigest()[:40], "jw" + _h(secret, b"jarvis-vr-reply").hexdigest()[:40])

# ------------------------------------------------------------------ JVR1 sealing (relay)
def _keys(secret): return _h(secret, b"jarvis-vr-enc").digest(), _h(secret, b"jarvis-vr-mac").digest()

def _stream(ek, nonce, n):
    out = bytearray(); c = 0
    while len(out) < n:
        out += hmac.new(ek, nonce + c.to_bytes(8, "big"), hashlib.sha256).digest(); c += 1
    return bytes(out[:n])

def _xor(a, b):
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(len(a), "little") if a else b""

def seal_bytes(data, secret):
    ek, mk = _keys(secret)
    nonce = secrets.token_bytes(16)
    ct = _xor(data, _stream(ek, nonce, len(data)))
    return BMAGIC + nonce + ct + hmac.new(mk, nonce + ct, hashlib.sha256).digest()

def unseal_bytes(blob, secret):
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < 52 or blob[:4] != BMAGIC:
        return None
    nonce, ct, tag = blob[4:20], blob[20:-32], blob[-32:]
    ek, mk = _keys(secret)
    if not hmac.compare_digest(tag, hmac.new(mk, nonce + ct, hashlib.sha256).digest()):
        return None
    return _xor(bytes(ct), _stream(ek, nonce, len(ct)))

def seal(obj, secret):
    raw = seal_bytes(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), secret)
    return MAGIC + base64.urlsafe_b64encode(raw[4:]).decode().rstrip("=")

def unseal(token, secret):
    try:
        if not isinstance(token, str) or not token.startswith(MAGIC) or len(token) > 20000:
            return None
        raw = token[len(MAGIC):].strip()
        pt = unseal_bytes(BMAGIC + base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), secret)
        obj = json.loads(pt.decode("utf-8")) if pt is not None else None
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None

# ------------------------------------------------------------------ audio framing
def audio_frame(kind, uid, seq, payload, flags=0):
    """Binary frame: b'JVA1' | kind u8 | flags u8 | seq u16 BE | utterance id u32 BE | payload."""
    return AMAGIC + struct.pack(">BBHI", kind, flags, seq & 0xFFFF, uid & 0xFFFFFFFF) + payload

def parse_audio_frame(b):
    if not isinstance(b, (bytes, bytearray)) or len(b) < 12 or b[:4] != AMAGIC:
        return None
    kind, flags, seq, uid = struct.unpack(">BBHI", bytes(b[4:12]))
    if kind not in (A_PCM16, A_ULAW, A_WAV):
        return None
    return {"kind": kind, "flags": flags, "seq": seq, "id": uid, "data": bytes(b[12:])}

def _ulaw_table():
    t = []
    for i in range(256):
        u = ~i & 0xFF
        sign, exp, man = u & 0x80, (u >> 4) & 7, u & 0x0F
        s = ((man << 3) + 0x84) << exp
        s -= 0x84
        t.append(-s if sign else s)
    return t
_ULAW = _ulaw_table()

def ulaw_decode(data):
    """G.711 mu-law bytes -> little-endian int16 PCM bytes (pure Python; audioop is deprecated)."""
    return struct.pack("<%dh" % len(data), *(_ULAW[b] for b in data))

def ulaw_encode(pcm):
    n = len(pcm) // 2
    out = bytearray(n)
    for i, s in enumerate(struct.unpack("<%dh" % n, pcm[:2 * n])):
        sign = 0x80 if s < 0 else 0
        s = min(32635, -s if s < 0 else s) + 0x84
        exp = 7
        while exp > 0 and not (s & (0x4000 >> (7 - exp))):
            exp -= 1
        man = (s >> (exp + 3)) & 0x0F
        out[i] = ~(sign | (exp << 4) | man) & 0xFF
    return bytes(out)

class Utterance:
    """Collects one push-to-talk: frames in order, at most MAX_UTTER_S seconds. -> pcm16 bytes at 16 kHz."""
    def __init__(self, uid, kind=A_PCM16, rate=RATE):
        self.id, self.kind, self.rate = uid, kind, rate
        self.parts = {}; self.bytes = 0; self.too_long = False; self.t0 = time.monotonic()

    def add(self, fr):
        if fr["id"] != self.id or fr["kind"] != self.kind or self.too_long:
            return False
        pcm = ulaw_decode(fr["data"]) if self.kind == A_ULAW else fr["data"]
        if self.bytes + len(pcm) > MAX_UTTER_S * self.rate * 2:
            self.too_long = True; return False
        if fr["seq"] in self.parts:
            return False
        self.parts[fr["seq"]] = pcm; self.bytes += len(pcm)
        return True

    def missing(self):
        if not self.parts: return 0
        return max(self.parts) + 1 - len(self.parts)

    def pcm(self):
        return b"".join(self.parts[k] for k in sorted(self.parts))

def resample_pcm16(pcm, src, dst=RATE):
    """Nearest-sample-free linear resampling (numpy when present). For the relay's smaller audio."""
    if src == dst:
        return pcm
    import numpy as np
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if len(x) == 0:
        return b""
    n = int(len(x) * dst / src)
    y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()

def wav_info(wav):
    """-> (rate, channels, bits, pcm bytes) of a simple RIFF WAV, or None."""
    try:
        if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
            return None
        i = 12; fmt = None
        while i + 8 <= len(wav):
            cid, n = wav[i:i + 4], struct.unpack("<I", wav[i + 4:i + 8])[0]
            body = wav[i + 8:i + 8 + n]
            if cid == b"fmt ":
                _, ch, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16]); fmt = (rate, ch, bits)
            elif cid == b"data" and fmt:
                return fmt + (body,)
            i += 8 + n + (n & 1)
    except Exception:
        return None
    return None

def make_wav(pcm, rate=RATE):
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)

def wav_to_ulaw16k(wav):
    info = wav_info(wav)
    if not info or info[2] != 16:
        return None
    rate, ch, _bits, pcm = info
    if ch == 2:
        import numpy as np
        pcm = np.frombuffer(pcm, dtype="<i2").reshape(-1, 2).mean(axis=1).astype("<i2").tobytes()
    return ulaw_encode(resample_pcm16(pcm, rate, RATE))

# ------------------------------------------------------------------ network helpers
def lan_ip(connect=None):
    """This PC's private LAN address (the one its default route uses), or None. Nothing is sent: a UDP connect()
    only picks the route."""
    try:
        if connect is not None:
            ip = connect()
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("10.255.255.255", 1)); ip = s.getsockname()[0]
            finally:
                s.close()
        a = ipaddress.ip_address(ip)
        if a.is_private and not a.is_loopback and not a.is_link_local:
            return ip
    except Exception:
        pass
    return None

def is_lan_peer(ip):
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        return a.is_private and not a.is_link_local or a.is_loopback
    except Exception:
        return False

def make_cert(folder, ip):
    """Self-signed ECDSA P-256 cert for wss://<ip>:8771 (SAN = the IP). -> (cert path, key path). Regenerated when the
    IP changes. Needs the cryptography package (in requirements.txt since 6.x)."""
    os.makedirs(folder, exist_ok=True)
    cp, kp, mp = (os.path.join(folder, n) for n in ("cert.pem", "key.pem", "cert.json"))
    try:
        with open(mp, encoding="utf-8") as f:
            meta = json.load(f)
        if meta.get("ip") == ip and os.path.exists(cp) and os.path.exists(kp):
            return cp, kp
    except Exception:
        pass
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "JARVIS VR bridge " + ip)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    with os.fdopen(os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with open(cp, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    fp = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    with open(mp, "w", encoding="utf-8") as f:
        json.dump({"ip": ip, "sha256": fp, "made": int(time.time())}, f)
    return cp, kp

def cert_fingerprint(folder):
    try:
        with open(os.path.join(folder, "cert.json"), encoding="utf-8") as f:
            return json.load(f).get("sha256")
    except Exception:
        return None

# ------------------------------------------------------------------ RFC 6455 (server side, text + binary)
def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()

def encode_frame(data, opcode=None):
    if isinstance(data, str):
        data = data.encode("utf-8"); opcode = opcode or 1
    opcode = opcode or 2
    n = len(data)
    head = bytes([0x80 | opcode])
    if n < 126: head += bytes([n])
    elif n < 65536: head += bytes([126]) + struct.pack(">H", n)
    else: head += bytes([127]) + struct.pack(">Q", n)
    return head + data

def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return bytes(buf)

def unmask(data, k):
    if not data:
        return b""
    kk = (k * (len(data) // 4 + 1))[:len(data)]
    return _xor(data, kk)

def read_frame(sock, limit=1 << 20):
    """-> (opcode, payload). Client frames must be masked (RFC 6455); fragments are joined."""
    payload = b""; op0 = None
    while True:
        b1, b2 = _recv_exact(sock, 2)
        fin, op, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
        if n == 126: n = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif n == 127: n = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        if n + len(payload) > limit:
            raise ConnectionError("frame too big")
        if not b2 & 0x80:
            raise ConnectionError("unmasked client frame")
        k = _recv_exact(sock, 4)
        data = unmask(_recv_exact(sock, n), k) if n else b""
        if op >= 8:                      # control frames may arrive between fragments
            return op, data
        if op0 is None:
            op0 = op
        payload += data
        if fin:
            return op0, payload

def mask_frame(data, opcode=None):
    """Client-side frame (for tests and the self-test)."""
    if isinstance(data, str):
        data = data.encode(); opcode = opcode or 1
    opcode = opcode or 2
    n = len(data); head = bytes([0x80 | opcode])
    if n < 126: head += bytes([0x80 | n])
    elif n < 65536: head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else: head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    k = os.urandom(4)
    return head + k + unmask(data, k)

TRUST_PAGE = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>JARVIS VR: trusted</title><style>body{background:#0b0614;color:#e9dcff;font:20px system-ui;text-align:center;padding:12vh 6vw}
b{color:#b46cff;font-size:44px}a{color:#c9a2ff}</style></head><body><b>&#x2713; JARVIS trusts this headset</b>
<p>This PC (%s) is now allowed for JARVIS VR.</p><p>Close this tab and go back to <a href="%s">JARVIS VR</a>.</p>
<p style="font-size:14px;opacity:.6">Certificate %s</p></body></html>"""

# ------------------------------------------------------------------ store
class Store:
    """~/.local/share/jarvis/vr/vr.json (0600): paired devices {id: {name, secret (b64), paired}}, relay message count per day."""
    def __init__(self, path):
        self.path = path; self.lock = threading.Lock(); self.d = {}
        try:
            with open(path, encoding="utf-8") as f:
                self.d = json.load(f) or {}
        except Exception:
            self.d = {}
        self.d.setdefault("devices", {})

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # device secrets: owner only
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.d, f, indent=1)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)

    def add(self, name):
        dev = "q" + secrets.token_hex(4); secret = secrets.token_bytes(32)
        self.d["devices"][dev] = {"name": str(name or "Quest")[:40], "secret": b64(secret), "paired": int(time.time())}
        self.save()
        return dev, secret

    def secret(self, dev):
        e = self.d["devices"].get(str(dev))
        return unb64(e["secret"]) if e else None

    def devices(self):
        return dict(self.d["devices"])

    def clear(self):
        n = len(self.d["devices"]); self.d["devices"] = {}; self.save(); return n

    def count_relay(self, today=None):
        today = today or time.strftime("%Y-%m-%d")
        r = self.d.get("relay_count") or {}
        if r.get("day") != today:
            r = {"day": today, "n": 0}
        r["n"] += 1; self.d["relay_count"] = r
        return r["n"]

# ------------------------------------------------------------------ pairing code
class PairCode:
    def __init__(self, clock=time.time):
        self.clock = clock; self.code = None; self.until = 0; self.tries = 0; self.ck = None

    def new(self):
        self.code = "%06d" % secrets.randbelow(1_000_000)
        self.until = self.clock() + CODE_TTL; self.tries = 0; self.ck = code_key(self.code)
        return self.code

    def active(self):
        return bool(self.code) and self.clock() < self.until and self.tries < CODE_TRIES

    def check(self, pub, mac):
        """-> True when the request MAC matches the live code. Wrong tries count; the 5th kills the code."""
        if not self.active():
            return False
        if hmac.compare_digest(pair_request_mac(self.ck, pub), mac):
            return True
        self.tries += 1
        if self.tries >= CODE_TRIES:
            log.warning("VR pairing code burned after %d wrong tries", self.tries)
        return False

    def spend(self):
        self.code = None; self.until = 0; self.ck = None

    def spoken(self):
        return " ".join(self.code[:3]) + ", " + " ".join(self.code[3:]) if self.code else ""

# ------------------------------------------------------------------ hooks (app integration)
class Hooks:
    """Everything PC-specific. vr71_wire.AppHooks fills these in; tests use a fake."""
    sir = "sir"
    def transcribe(self, pcm16, rate=RATE): return ""
    def run(self, text, ask): return None
    def stats(self): return {}
    def now_playing(self): return {}
    def media(self, op): return False
    def hub(self): return []
    def fly(self, kind, side=None): return {}
    def in_game(self): return False
    def say_pc(self, text): pass
    def version(self): return "jarvis-os"
    def pc_name(self): return socket.gethostname()

HUB = [  # label, the exact words JARVIS hears (buttons go through the same parser as speech; built-in JARVIS OS commands)
    ("Weather", "what's the weather"), ("What time is it", "what time is it"), ("Open workspace", "open the workspace"),
    ("Show vitals", "show vitals"), ("Graphics check", "check my graphics card"), ("Gaming check", "run a gaming check"),
    ("Game mode", "game mode"), ("Clip that", "clip that"), ("Setup status", "how's setup going"),
    ("Next update", "when is the next update"), ("Show your brain", "show your brain"), ("What can you do", "what can you do"),
]
MEDIA_OPS = ("play_pause", "next", "previous")
FLY_KINDS = ("food", "wind", "shadow", "light")

# ------------------------------------------------------------------ sessions
class Session:
    """One connected headset (wss or relay). send_json / send_audio are transport-specific."""
    transport = "?"
    def __init__(self, bridge):
        self.bridge = bridge; self.dev = None; self.authed = False; self.utt = None
        self.pending_ask = None; self.closed = False; self.audio_ok = True
    def send_json(self, obj): raise NotImplementedError
    def send_audio(self, uid, wav, text=""): raise NotImplementedError

class WSSession(Session):
    transport = "lan"
    def __init__(self, bridge, sock, peer):
        # one lock for ALL socket I/O: an ssl socket must never be read and written from two threads at once
        # (seen in tests: a reply sent while the reader sat in recv() broke the TLS stream)
        super().__init__(bridge); self.sock = sock; self.peer = peer; self.wlock = threading.RLock()
        self.nonce = secrets.token_hex(16)
    def readable(self, timeout):
        pend = getattr(self.sock, "pending", None)
        if pend and pend():
            return True
        try:
            r, _, _ = select.select([self.sock], [], [], timeout)
        except (OSError, ValueError):
            raise ConnectionError("closed")
        return bool(r)
    def read(self, limit=1 << 20):
        with self.wlock:
            return read_frame(self.sock, limit=limit)
    def _send(self, frame):
        with self.wlock:
            self.sock.sendall(frame)
    def send_json(self, obj):
        try: self._send(encode_frame(json.dumps(obj, ensure_ascii=False)))
        except Exception: self.closed = True
    def send_audio(self, uid, wav, text=""):
        try: self._send(encode_frame(audio_frame(A_WAV, uid, 0, wav)))
        except Exception: self.closed = True

class RelaySession(Session):
    transport = "relay"
    def __init__(self, bridge, dev, secret, relay):
        super().__init__(bridge); self.dev = dev; self.secret = secret; self.relay = relay; self.authed = True
        self.batch = []; self.block = threading.Lock(); self.timer = None
    def send_json(self, obj):
        self.relay.post(self.secret, obj)
    def send_audio(self, uid, wav, text=""):
        """Relay: spoken lines within BATCH_S are merged into one message (one mu-law attachment)."""
        u = wav_to_ulaw16k(wav) if wav else None
        with self.block:
            self.batch.append((text, u))
            if self.timer is None:
                self.timer = threading.Timer(self.bridge.batch_s, self.flush); self.timer.daemon = True; self.timer.start()
    def flush(self):
        with self.block:
            items, self.batch, self.timer = self.batch, [], None
        if not items:
            return
        audio = b"".join(u for _, u in items if u)
        obj = {"t": "lines", "text": [t for t, _ in items]}
        self.relay.post(self.secret, obj, attach=audio_frame(A_ULAW, 0, 0, audio) if audio else None)

# ------------------------------------------------------------------ the bridge
class Bridge:
    def __init__(self, data_dir, hooks, cfg=None, clock=time.time, start_threads=True):
        self.data = os.path.join(data_dir, "vr"); self.h = hooks; self.cfg = dict(cfg or {})
        self.store = Store(os.path.join(self.data, "vr.json"))
        self.code = PairCode(clock); self.clock = clock
        self.sessions = []; self.slock = threading.Lock()
        self.port = int(self.cfg.get("port", PORT)); self.ip = None; self.server = None; self.ssl_ctx = None
        self.origins = tuple(self.cfg.get("origins") or ORIGINS) + tuple(self.cfg.get("extra_origins") or ())
        self.relay = Relay(self, servers=tuple(self.cfg.get("relay_servers") or RELAY_SERVERS))
        self.headset_only = bool(self.cfg.get("headset_only_audio", True))
        self.batch_s = float(self.cfg.get("relay_batch_s", 1.5))
        self.start_threads = start_threads
        self.stop_ev = threading.Event()
        self.last_error = None

    # ---- status
    def connected(self):
        with self.slock:
            self.sessions = [s for s in self.sessions if not s.closed]
            return [s for s in self.sessions if s.authed]

    def status(self):
        live = self.connected()
        return {"paired": len(self.store.devices()), "connected": [s.transport for s in live], "ip": self.ip,
                "port": self.port, "lan": self.server is not None, "code_active": self.code.active(),
                "cert": cert_fingerprint(self.data), "error": self.last_error}

    def address(self):
        return f"{self.ip}:{self.port}" if self.ip else None

    # ---- pairing
    def start_pairing(self):
        """-> (code, address or None). Starts the LAN server and the relay pairing listener (5 minutes)."""
        self.start_lan()
        code = self.code.new()
        self.relay.listen_pairing(self.code.ck)
        return code, self.address()

    def unpair(self):
        n = self.store.clear()
        self.code.spend()
        for s in self.connected():
            s.send_json({"t": "unpaired"}); s.closed = True
            if isinstance(s, WSSession):
                try: s.sock.close()
                except Exception: pass
        self.relay.reload()
        return n

    def handle_pair(self, msg):
        """A 'pair' request from either transport. -> reply dict."""
        try:
            pub, mac = unb64(msg.get("pub", "")), unb64(msg.get("mac", ""))
        except Exception:
            return {"t": "pair_fail", "why": "bad request"}
        if not self.code.active():
            return {"t": "pair_fail", "why": "no code" if not self.code.code else "code expired"}
        if not self.code.check(pub, mac):
            return {"t": "pair_fail", "why": "wrong code", "left": max(0, CODE_TRIES - self.code.tries)}
        ck = self.code.ck
        dev, secret = self.store.add(msg.get("name") or "Quest")
        try:
            ans = pair_answer(ck, pub, secret, dev)
        except ValueError:
            self.store.d["devices"].pop(dev, None); self.store.save()
            return {"t": "pair_fail", "why": "bad key"}
        self.code.spend()
        ans.update(pc=self.h.pc_name(), v=self.h.version(), lan=self.address())
        self.relay.reload()
        try: self.h.say_pc(f"Headset paired, {self.h.sir}.")
        except Exception: pass
        log.info("VR headset paired: %s", dev)
        return ans

    # ---- LAN server
    def start_lan(self):
        if self.server is not None:
            return True
        ip = self.cfg.get("bind_ip") or lan_ip()
        if not ip:
            self.last_error = "no private LAN address (relay only)"; return False
        try:
            cp, kp = make_cert(self.data, ip)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cp, kp)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((ip, self.port)); s.listen(8)
        except Exception as e:
            self.last_error = f"LAN server: {e}"; log.warning("VR LAN server not started: %s", e)
            return False
        self.ip, self.server, self.ssl_ctx = ip, s, ctx
        self.last_error = None
        if self.start_threads:
            threading.Thread(target=self._accept_loop, daemon=True, name="vr-accept").start()
        log.info("VR bridge listening on wss://%s:%d", ip, self.port)
        return True

    def stop_lan(self):
        """Stop listening on the LAN (the relay is separate). Used when a pairing code ran out with no headset paired,
        so nothing stays open on port 8771 that nobody can use."""
        s, self.server = self.server, None
        if s is not None:
            try: s.shutdown(socket.SHUT_RDWR)          # wakes the accept() thread; close() alone leaves the port bound
            except Exception: pass
            try: s.close()
            except Exception: pass
            log.info("VR bridge stopped listening on the LAN")

    def stop(self):
        self.stop_ev.set()
        s, self.server = self.server, None
        if s is not None:
            try: s.shutdown(socket.SHUT_RDWR)
            except Exception: pass
            try: s.close()
            except Exception: pass
        self.relay.stop()

    def _accept_loop(self):
        while not self.stop_ev.is_set() and self.server is not None:
            try:
                c, addr = self.server.accept()
            except OSError:
                return
            if not is_lan_peer(addr[0]):
                c.close(); continue
            threading.Thread(target=self._serve_tls, args=(c, addr[0]), daemon=True).start()

    def _serve_tls(self, raw, peer):
        try:
            raw.settimeout(10)
            c = self.ssl_ctx.wrap_socket(raw, server_side=True)
        except Exception as e:
            log.debug("VR TLS handshake from %s failed: %s (expected until the headset trusts the cert)", peer, e)
            try: raw.close()
            except Exception: pass
            return
        self.serve_conn(c, peer)

    def http_reply(self, path, origin):
        """Plain https GETs on the bridge port: / (the trust page) and /ping (JSON, CORS for the app's origin)."""
        if path.startswith("/ping"):
            body = json.dumps({"jarvis": True, "v": self.h.version(), "pc": self.h.pc_name(), "vr": 1}).encode()
            ao = origin if origin in self.origins else self.origins[0]
            return (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: " + ao.encode() +
                    b"\r\nAccess-Control-Allow-Private-Network: true\r\nCache-Control: no-store\r\nContent-Length: "
                    + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        body = (TRUST_PAGE % (self.address() or "?", APP_URL, (cert_fingerprint(self.data) or "")[:16])).encode()
        return (b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nContent-Length: "
                + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)

    def serve_conn(self, c, peer):
        """One TCP/TLS connection: an http GET (trust page / ping / CORS preflight) or a WebSocket."""
        try:
            c.settimeout(10)
            req = b""
            while b"\r\n\r\n" not in req:
                chunk = c.recv(4096)
                if not chunk or len(req) > 16384:
                    raise ConnectionError("bad request")
                req += chunk
            lines = req.decode("latin-1").split("\r\n")
            method, path = (lines[0].split(" ") + ["", ""])[:2]
            hdr = {}
            for l in lines[1:]:
                if ":" in l:
                    k, v = l.split(":", 1); hdr[k.strip().lower()] = v.strip()
            origin = hdr.get("origin", "")
            if method == "OPTIONS":
                ao = origin if origin in self.origins else self.origins[0]
                c.sendall(b"HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: " + ao.encode() +
                          b"\r\nAccess-Control-Allow-Methods: GET\r\nAccess-Control-Allow-Private-Network: true\r\n"
                          b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                c.close(); return
            if "websocket" not in hdr.get("upgrade", "").lower():
                c.sendall(self.http_reply(path, origin)); c.close(); return
            if origin and origin not in self.origins:
                c.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"); c.close()
                log.info("VR bridge refused origin %s", origin); return
            key = hdr.get("sec-websocket-key")
            if not key:
                c.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n"); c.close(); return
            c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       "Sec-WebSocket-Accept: %s\r\n\r\n" % accept_key(key)).encode())
        except Exception as e:
            log.debug("VR request from %s: %s", peer, e)
            try: c.close()
            except Exception: pass
            return
        s = WSSession(self, c, peer)
        with self.slock:
            self.sessions.append(s)
        s.send_json({"t": "challenge", "n": s.nonce, "pc": self.h.pc_name(), "v": self.h.version()})
        try:
            c.settimeout(15)                 # a started frame must finish within 15 s
            last = time.time()
            while not s.closed and not self.stop_ev.is_set():
                if not s.readable(0.25):
                    if time.time() - last > IDLE_S:
                        raise ConnectionError("idle")
                    continue
                op, data = s.read(limit=1 << 20)
                last = time.time()
                if op == 8:
                    break
                if op == 9:
                    s._send(encode_frame(data, opcode=10)); continue
                if op == 1:
                    try:
                        msg = json.loads(data.decode("utf-8"))
                    except Exception:
                        continue
                    if isinstance(msg, dict):
                        self.on_json(s, msg)
                elif op == 2:
                    self.on_binary(s, data)
        except Exception as e:
            log.debug("VR session %s ended: %s", peer, e)
        finally:
            s.closed = True
            try: c.close()
            except Exception: pass

    # ---- messages (both transports)
    def on_json(self, s, msg):
        t = msg.get("t")
        if t == "pair":
            s.send_json(self.handle_pair(msg)); return
        if not s.authed:
            if t == "auth":
                sec = self.store.secret(msg.get("dev"))
                if sec and hmac.compare_digest(auth_mac(sec, getattr(s, "nonce", "")), str(msg.get("mac", ""))):
                    s.authed = True; s.dev = msg.get("dev")
                    s.send_json({"t": "auth_ok", "pc": self.h.pc_name(), "v": self.h.version(), "sir": self.h.sir})
                    self.send_state(s)
                    log.info("VR headset %s connected (%s)", s.dev, s.transport)
                else:
                    s.send_json({"t": "auth_fail", "why": "not paired with this PC" if not sec else "bad proof"})
                    s.closed = True
            return
        if t == "ping":
            s.send_json({"t": "pong", "ts": msg.get("ts")})
        elif t == "ptt":
            self.on_ptt(s, msg)
        elif t == "cmd":
            text = str(msg.get("text") or "").strip()[:300]
            if text:
                self.spawn(self.run_text, s, text)
        elif t == "media":
            op = msg.get("op")
            if op in MEDIA_OPS:
                ok = self.h.media(op)
                s.send_json({"t": "toast", "text": {"play_pause": "Play/pause", "next": "Next", "previous": "Previous"}[op] + (" sent" if ok is not False else " not available")})
                self.spawn(lambda: (time.sleep(1.2), self.send_state(s, only=("now",))))
        elif t == "state":
            self.send_state(s, only=msg.get("only"))
        elif t == "fly":
            kind = msg.get("kind")
            if kind in FLY_KINDS:
                side = msg.get("side") if msg.get("side") in ("l", "r") else None
                self.spawn(self.run_fly, s, kind, side)
        elif t == "confirm":
            pa = s.pending_ask
            if pa and str(msg.get("id")) == pa["id"]:
                pa["answer"] = "yes" if msg.get("answer") == "yes" else "no"; pa["ev"].set()

    def on_ptt(self, s, msg):
        op = msg.get("op")
        if op == "start":
            kind = A_ULAW if msg.get("fmt") == "ulaw" else A_PCM16
            s.utt = Utterance(int(msg.get("id") or 0), kind, int(msg.get("rate") or RATE))
            s.utt.mode = "tab" if msg.get("mode") == "tab" else "cmd"
        elif op == "cancel":
            s.utt = None
        elif op == "end" and s.utt is not None:
            u, s.utt = s.utt, None
            if msg.get("mode") in ("tab", "cmd"):
                u.mode = msg["mode"]
            self.spawn(self.finish_utterance, s, u)

    def on_binary(self, s, data):
        if not s.authed:
            return
        fr = parse_audio_frame(data)
        if fr and s.utt is not None:
            s.utt.add(fr)

    def finish_utterance(self, s, u):
        pcm = u.pcm()
        if u.rate != RATE:
            pcm = resample_pcm16(pcm, u.rate, RATE)
        secs = len(pcm) / 2 / RATE
        if secs < 0.3:
            s.send_json({"t": "heard", "text": "", "why": "too short"}); return None
        text = ""
        try:
            text = (self.h.transcribe(pcm, RATE) or "").strip()
        except Exception as e:
            log.exception("VR transcribe")
            s.send_json({"t": "err", "text": f"Speech recognition failed: {str(e)[:80]}"}); return None
        pa = s.pending_ask
        if pa and not pa["ev"].is_set():          # an open "are you sure?" takes this utterance as its answer
            s.send_json({"t": "heard", "text": text, "secs": round(secs, 1), "missing": u.missing()})
            pa["answer"] = text or "no"; pa["ev"].set(); return text
        if not text:
            s.send_json({"t": "heard", "text": "", "why": "didn't catch that"}); return None
        if getattr(u, "mode", "cmd") == "tab" and not text.lower().startswith(("ask tab", "tab")):
            text = "ask Tab " + text
        s.send_json({"t": "heard", "text": text, "secs": round(secs, 1), "missing": u.missing()})
        self.run_text(s, text)
        return text

    def make_ask(self, s, timeout=25.0):
        def ask(q):
            pa = {"id": secrets.token_hex(4), "ev": threading.Event(), "answer": None}
            s.pending_ask = pa
            try:
                self.h.say_pc(q)                 # spoken (and mirrored to the headset by the voice tap)
                s.send_json({"t": "ask", "id": pa["id"], "q": q})
                pa["ev"].wait(timeout)
                return pa["answer"] or "no"
            finally:
                s.pending_ask = None
        return ask

    def run_text(self, s, text):
        self._active = s
        try:
            self.h.run(text, self.make_ask(s))
        except Exception as e:
            log.exception("VR command")
            s.send_json({"t": "err", "text": f"That command failed: {str(e)[:80]}"})

    def run_fly(self, s, kind, side):
        s.send_json({"t": "fly_busy", "kind": kind})
        try:
            resp = self.h.fly(kind, side) or {}
        except Exception as e:
            resp = {"kind": kind, "source": "stylised", "note": f"stylised (the sim failed: {str(e)[:60]})"}
        s.send_json({"t": "fly", "resp": json.loads(json.dumps(resp, default=str))})

    def send_state(self, s, only=None):
        st = {"t": "state", "game": bool(self.h.in_game())}
        want = set(only or ("stats", "now", "hub"))
        try:
            if "stats" in want: st["stats"] = self.h.stats() or {}
            if "now" in want: st["now"] = self.h.now_playing() or {}
            if "hub" in want: st["hub"] = [{"label": a, "cmd": b} for a, b in (self.h.hub() or HUB)]
        except Exception:
            log.exception("VR state")
        s.send_json(st)

    def spawn(self, fn, *a):
        if not self.start_threads:
            return fn(*a)
        threading.Thread(target=fn, args=a, daemon=True).start()

    # ---- the voice tap: everything JARVIS says also goes to the headset
    def on_line(self, text, wav):
        """Called by Voice for each line it is about to play. -> True when a headset is taking the audio and the PC
        speakers should stay quiet (vr.headset_only_audio, default on while a headset is connected)."""
        live = self.connected()
        if not live:
            return False
        uid = secrets.randbelow(1 << 31)
        for s in live:
            if isinstance(s, RelaySession):
                s.send_audio(uid, wav, text)
            else:
                s.send_json({"t": "line", "text": text, "id": uid, "audio": bool(wav)})
                if wav:
                    s.send_audio(uid, wav, text)
        return self.headset_only and any(isinstance(s, WSSession) for s in live) and bool(wav)

# ------------------------------------------------------------------ relay (ntfy)
class Relay:
    """Sealed messages on ntfy topics derived from each device secret (and, while a code is live, the pairing topics).
    One streaming GET per topic set; posts are plain https. opener/poster are injectable for tests."""
    def __init__(self, bridge, servers=RELAY_SERVERS, opener=None, poster=None):
        self.b = bridge; self.servers = tuple(servers); self.server = self.servers[0]
        self.opener = opener or urllib.request.urlopen; self.poster = poster or self._post_http
        self.sessions = {}; self.seen = {}; self.pair_ck = None; self.pair_until = 0
        self.gen = 0; self.thread = None; self.stop_ev = threading.Event(); self.wake = threading.Event()
        self.resp = None

    def topics(self):
        """-> {topic: ("dev", dev_id, secret) | ("pair", None, ck)}"""
        out = {}
        for dev in self.b.store.devices():
            sec = self.b.store.secret(dev)
            out[device_topics(sec)[0]] = ("dev", dev, sec)
        if self.pair_ck is not None and time.time() < self.pair_until:
            out[pair_topics(self.pair_ck)[0]] = ("pair", None, self.pair_ck)
        return out

    def listen_pairing(self, ck):
        self.pair_ck = ck; self.pair_until = time.time() + CODE_TTL; self.reload()

    def reload(self):
        self.gen += 1
        r = self.resp
        if r is not None:
            self._abort(r)
        self.wake.set()
        self.start()

    @staticmethod
    def _abort(r):
        """End the streaming GET now. r.close() alone waits for the reader thread's buffer lock, i.e. until ntfy's next
        keepalive (seen in the rc5 VM test: "unpair my headset" took 23 s), so shut the socket down first; that wakes the
        reader at once, and the close runs on its own thread."""
        try:
            r.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        def close():
            try: r.close()
            except Exception: pass
        threading.Thread(target=close, daemon=True, name="vr-relay-close").start()

    def start(self):
        if self.b.start_threads and (self.thread is None or not self.thread.is_alive()):
            self.thread = threading.Thread(target=self._loop, daemon=True, name="vr-relay"); self.thread.start()

    def stop(self):
        self.stop_ev.set(); self.reload()

    def _loop(self):
        back = 2
        while not self.stop_ev.is_set():
            if not self.topics():
                self.wake.wait(60); self.wake.clear(); continue
            try:
                self.run_once(); back = 2
            except Exception as e:
                log.info("VR relay: %s (retry in %ss)", e, back)
                self.stop_ev.wait(back); back = min(120, back * 2)

    def run_once(self):
        tmap = self.topics()
        if not tmap:
            return False
        gen = self.gen
        url = f"{self.server}/{','.join(tmap)}/json?since={int(time.time()) - 30}"
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-vr/1.0"})
        with self.opener(req, timeout=90) as r:
            self.resp = r
            for line in r:
                if self.stop_ev.is_set() or gen != self.gen:
                    return True
                line = line.decode("utf-8", "replace").strip() if isinstance(line, bytes) else str(line).strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                try:
                    self.on_event(ev, tmap)
                except Exception:
                    log.exception("VR relay event")
        return True

    def _fetch(self, url):
        if not url.startswith(self.server + "/"):
            raise ValueError("attachment from another host")
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-vr/1.0"})
        with self.opener(req, timeout=30) as r:
            data = r.read(MAX_ATTACH + 1)
        if len(data) > MAX_ATTACH:
            raise ValueError("attachment too big")
        return data

    def on_event(self, ev, tmap=None):
        if ev.get("event") != "message":
            return None
        tmap = tmap if tmap is not None else self.topics()
        ent = tmap.get(ev.get("topic"))
        if not ent:
            return None
        kind, dev, key = ent
        if kind == "pair":
            try:
                msg = json.loads(ev.get("message") or "{}")
            except ValueError:
                return None
            if not isinstance(msg, dict) or msg.get("t") != "pair":
                return None
            out = self.b.handle_pair(msg)
            self.poster(self.server, pair_topics(key)[1], json.dumps(out).encode(), None)
            if out.get("t") == "paired":
                self.pair_ck = None
            return out
        msg = unseal(ev.get("message") or "", key)
        if not msg:
            return None
        ts, mid = msg.get("ts"), str(msg.get("mid") or "")
        if not isinstance(ts, (int, float)) or abs(time.time() - ts) > MAX_AGE_S or not mid or mid in self.seen:
            return None
        self.seen[mid] = time.time()
        if len(self.seen) > 500:
            for k in sorted(self.seen, key=self.seen.get)[:250]:
                self.seen.pop(k, None)
        s = self.sessions.get(dev)
        if s is None or s.closed:
            s = RelaySession(self.b, dev, key, self)
            self.sessions[dev] = s
            with self.b.slock:
                self.b.sessions.append(s)
        s.last = time.time()
        att = ev.get("attachment") or {}
        if msg.get("t") == "utter" and att.get("url"):
            blob = unseal_bytes(self._fetch(att["url"]), key)
            fr = parse_audio_frame(blob) if blob else None
            if not fr:
                return None
            u = Utterance(fr["id"], fr["kind"], int(msg.get("rate") or RATE))
            u.mode = "tab" if msg.get("mode") == "tab" else "cmd"
            u.add(fr)
            self.b.spawn(self.b.finish_utterance, s, u)
            return msg
        if msg.get("t") == "bye":
            s.closed = True; self.sessions.pop(dev, None); return msg
        if msg.get("t") == "hello":
            s.send_json({"t": "auth_ok", "pc": self.b.h.pc_name(), "v": self.b.h.version(), "sir": self.b.h.sir, "relay": True})
            self.b.send_state(s)
            return msg
        self.b.on_json(s, msg)
        return msg

    def post(self, secret, obj, attach=None):
        obj = dict(obj, ts=int(time.time()), mid=secrets.token_hex(6))
        n = self.b.store.count_relay()
        if n > int(self.b.cfg.get("relay_daily_budget", RELAY_DAILY_BUDGET)) and obj.get("t") not in ("auth_ok", "err", "lines"):
            return False
        topic = device_topics(secret)[1]
        tok = seal(obj, secret)
        try:
            self.poster(self.server, topic, tok.encode() if attach is None else seal_bytes(attach, secret),
                        tok if attach is not None else None)
            return True
        except Exception as e:
            log.warning("VR relay post failed: %s", e)
            return False

    @staticmethod
    def _post_http(server, topic, body, message=None):
        url = f"{server}/{topic}"
        if message is not None:              # binary body = attachment; the sealed JSON rides as the message
            url += "?" + urllib.parse.urlencode({"filename": "jvr.bin", "message": message})
        req = urllib.request.Request(url, data=body, method="PUT" if message is not None else "POST",
                                     headers={"User-Agent": "JARVIS-vr/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
