"""rc5: the JARVIS VR bridge on JARVIS OS (jarvisd/vrbridge.py, the same protocol as Windows JARVIS 7.1) and its wiring
(jarvisd/vrpair.py): pairing (6-digit code, 5 minutes, one use, 5 wrong tries burn it), a mock headset over real TLS,
audio framing, the ntfy relay fallback, the headset's own JavaScript run in node, "pair my headset" by voice, the voice
tap, the selftest, and the firewall rules that open port 8771 to the home network only."""
import base64, hashlib, json, math, os, pathlib, re, shutil, socket, ssl, struct, subprocess, sys, threading, time, types
import xml.etree.ElementTree as ET
import pytest
from jarvisd import vrbridge as V, vrpair as P, brain as B

ROOT = pathlib.Path(__file__).resolve().parents[2]
SITE = str(pathlib.Path(__file__).parent / "fixtures" / "vrapp")          # the VR app's own crypto JS (from the web app)
NODE_JS = str(pathlib.Path(__file__).parent / "fixtures" / "vr_node.js")
NODE = shutil.which("node")
need_node = pytest.mark.skipif(not NODE, reason="node not installed")

def node(cmd, payload, timeout=60):
    r = subprocess.run([NODE, NODE_JS, SITE, cmd], input=json.dumps(payload), capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class Hooks(V.Hooks):
    sir = "sir"
    def __init__(self):
        self.heard, self.ran, self.said, self.fly_calls, self.media_ops = [], [], [], [], []
        self.answer = "turn on the lights"; self.game = False
    def transcribe(self, pcm, rate=16000):
        self.heard.append(len(pcm)); return self.answer
    def run(self, text, ask):
        self.ran.append(text); self.last_ask = ask
    def stats(self): return {"cpu": 12.5, "gpu": 40}
    def now_playing(self): return {"title": "Song", "artist": "Band", "playing": True}
    def media(self, op): self.media_ops.append(op); return True
    def fly(self, kind, side=None): self.fly_calls.append((kind, side)); return {"kind": kind, "source": "connectome", "trail": [["a", 1.0, True]]}
    def in_game(self): return self.game
    def say_pc(self, t): self.said.append(t)
    def pc_name(self): return "TESTPC"


def bridge(tmp_path, **cfg):
    return V.Bridge(str(tmp_path), Hooks(), cfg, start_threads=False)


class Sess(V.Session):
    transport = "test"
    def __init__(self, b):
        super().__init__(b); self.out = []; self.audio = []; self.nonce = "n0nce"
    def send_json(self, o): self.out.append(o)
    def send_audio(self, uid, wav, text=""): self.audio.append((uid, wav))


def headset_pair(code):
    d, pub = V.ec_keypair()
    ck = V.code_key(code)
    return d, pub, ck, {"t": "pair", "pub": V.b64(pub), "mac": V.b64(V.pair_request_mac(ck, pub)), "name": "Quest 3"}


# ================================================================ crypto + pairing
def test_p256_ecdh_matches_cryptography():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    for _ in range(3):
        d, pub = V.ec_keypair()
        k = ec.generate_private_key(ec.SECP256R1())
        kp = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        theirs = k.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub))
        assert V.ec_shared(d, kp) == theirs
    with pytest.raises(ValueError):
        V.ec_shared(5, b"\x04" + b"\x01" * 64)          # not on the curve: refused


def test_pairing_code_six_digits_ttl_one_use_and_five_tries(tmp_path):
    now = [1000.0]
    pc = V.PairCode(clock=lambda: now[0])
    code = pc.new()
    assert re.fullmatch(r"\d{6}", code) and pc.active()
    _, pub, _, _ = headset_pair("000000" if code != "000000" else "111111")
    for i in range(4):
        assert not pc.check(pub, b"x" * 32)
    assert pc.active() and pc.tries == 4
    assert not pc.check(pub, b"x" * 32) and not pc.active()            # 5th wrong try burns it
    pc.new(); now[0] += V.CODE_TTL + 1
    assert not pc.active()                                              # 5 minutes
    assert pc.spoken() and "," in pc.spoken()


def test_pairing_exchange_gives_both_sides_the_same_secret(tmp_path):
    b = bridge(tmp_path)
    code, addr = b.start_pairing()
    d, pub, ck, msg = headset_pair(code)
    ans = b.handle_pair(msg)
    assert ans["t"] == "paired" and ans["pc"] == "TESTPC"
    dev, secret = V.pair_finish(ck, d, pub, ans)
    assert b.store.secret(dev) == secret and len(secret) == 32
    assert not b.code.active()                                          # one use
    assert b.handle_pair(msg)["t"] == "pair_fail"
    assert "secret" not in json.dumps(ans) and V.b64(secret) not in json.dumps(ans)   # never sent in the clear
    stored = json.load(open(tmp_path / "vr" / "vr.json"))
    assert dev in stored["devices"]
    assert b.h.said and "paired" in b.h.said[-1].lower()


def test_wrong_code_is_refused_with_tries_left(tmp_path):
    b = bridge(tmp_path)
    code, _ = b.start_pairing()
    wrong = "%06d" % ((int(code) + 1) % 1000000)
    _, _, _, msg = headset_pair(wrong)
    r = b.handle_pair(msg)
    assert r == {"t": "pair_fail", "why": "wrong code", "left": 4} and not b.store.devices()


def test_seal_roundtrip_tamper_and_binary(tmp_path):
    s = os.urandom(32)
    tok = V.seal({"t": "x", "u": "héllo ✓"}, s)
    assert tok.startswith("JVR1.") and V.unseal(tok, s) == {"t": "x", "u": "héllo ✓"}
    assert V.unseal(tok, os.urandom(32)) is None
    bad = tok[:-3] + ("A" if tok[-3] != "A" else "B") + tok[-2:]
    assert V.unseal(bad, s) is None
    blob = V.seal_bytes(b"\x00\x01" * 5000, s)
    assert blob[:4] == b"JVB1" and V.unseal_bytes(blob, s) == b"\x00\x01" * 5000 and V.unseal_bytes(blob[:-1] + b"x", s) is None


# ================================================================ audio framing
def test_audio_frames_and_utterance_reassembly():
    pcm = struct.pack("<1600h", *range(-800, 800))
    frames = [V.audio_frame(V.A_PCM16, 9, i, pcm) for i in range(5)]
    f0 = V.parse_audio_frame(frames[0])
    assert f0 == {"kind": 1, "flags": 0, "seq": 0, "id": 9, "data": pcm}
    assert V.parse_audio_frame(b"nope" + frames[0][4:]) is None and V.parse_audio_frame(b"JVA1") is None
    u = V.Utterance(9)
    for i in (0, 2, 1, 4):                                              # out of order, one missing
        assert u.add(V.parse_audio_frame(frames[i]))
    assert not u.add(V.parse_audio_frame(frames[2]))                    # duplicate ignored
    assert not u.add(V.parse_audio_frame(V.audio_frame(V.A_PCM16, 10, 3, pcm)))   # another utterance ignored
    assert u.missing() == 1 and u.pcm() == pcm * 4


def test_utterance_caps_at_30_seconds():
    u = V.Utterance(1)
    big = b"\x00" * (16000 * 2)                                          # one second
    for i in range(30):
        assert u.add({"kind": 1, "id": 1, "seq": i, "data": big})
    assert not u.add({"kind": 1, "id": 1, "seq": 31, "data": big}) and u.too_long


def test_ulaw_wav_helpers():
    x = [int(12000 * math.sin(i / 7)) for i in range(2000)]
    pcm = struct.pack("<2000h", *x)
    back = struct.unpack("<2000h", V.ulaw_decode(V.ulaw_encode(pcm)))
    assert max(abs(a - b) for a, b in zip(x, back)) <= 512
    wav = V.make_wav(pcm, 24000)
    rate, ch, bits, data = V.wav_info(wav)
    assert (rate, ch, bits, data) == (24000, 1, 16, pcm)
    u = V.wav_to_ulaw16k(wav)
    assert abs(len(u) - 2000 * 16000 // 24000) <= 1


# ================================================================ mock websocket over real TLS on loopback
class WSClient:
    def __init__(self, port, origin="https://jarvis-vr.sites.tab.bot"):
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.s = ctx.wrap_socket(raw, server_hostname="127.0.0.1")
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                        f"Sec-WebSocket-Version: 13\r\nOrigin: {origin}\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(1)
        self.status = resp.split(b"\r\n")[0]
        if b"101" in self.status:
            assert V.accept_key(key).encode() in resp
    def send(self, obj): self.s.sendall(V.mask_frame(json.dumps(obj)))
    def send_bin(self, b): self.s.sendall(V.mask_frame(b, opcode=2))
    def recv(self):
        b1, b2 = V._recv_exact(self.s, 2); n = b2 & 0x7F
        if n == 126: n = struct.unpack(">H", V._recv_exact(self.s, 2))[0]
        elif n == 127: n = struct.unpack(">Q", V._recv_exact(self.s, 8))[0]
        data = V._recv_exact(self.s, n)
        return (json.loads(data) if b1 & 0x0F == 1 else data)
    def recv_until(self, t, limit=20):
        for _ in range(limit):
            m = self.recv()
            if isinstance(m, dict) and m.get("t") == t:
                return m
        raise AssertionError("no " + t)


@pytest.fixture
def live(tmp_path):
    so = socket.socket(); so.bind(("127.0.0.1", 0)); port = so.getsockname()[1]; so.close()
    b = V.Bridge(str(tmp_path), Hooks(), {"bind_ip": "127.0.0.1", "port": port})
    b.relay.start = lambda: None                                        # no network in tests
    assert b.start_lan()
    yield b, port
    b.stop()


def test_mock_websocket_pairing_auth_ptt_and_reply_audio(live):
    b, port = live
    code, addr = b.start_pairing()
    assert addr == f"127.0.0.1:{port}" and os.path.exists(os.path.join(b.data, "cert.pem"))
    c = WSClient(port)
    ch = c.recv()
    assert ch["t"] == "challenge" and ch["pc"] == "TESTPC" and ch["v"] == "jarvis-os"
    d, pub, ck, msg = headset_pair(code)
    c.send(msg)
    ans = c.recv_until("paired")
    dev, secret = V.pair_finish(ck, d, pub, ans)
    c.s.close()
    # a fresh connection authenticates with the device secret
    c = WSClient(port); ch = c.recv()
    c.send({"t": "auth", "dev": dev, "mac": V.auth_mac(secret, ch["n"])})
    ok = c.recv_until("auth_ok")
    st = c.recv_until("state")
    assert ok["pc"] == "TESTPC" and st["stats"]["cpu"] == 12.5 and st["now"]["title"] == "Song" and len(st["hub"]) == 12
    # push-to-talk: 1 s of PCM in 10 frames
    pcm = struct.pack("<1600h", *([300] * 1600))
    c.send({"t": "ptt", "op": "start", "id": 77, "fmt": "pcm16", "rate": 16000})
    for i in range(10):
        c.send_bin(V.audio_frame(V.A_PCM16, 77, i, pcm))
    c.send({"t": "ptt", "op": "end", "id": 77})
    heard = c.recv_until("heard")
    assert heard["text"] == "turn on the lights" and heard["secs"] == 1.0 and heard["missing"] == 0
    for _ in range(50):
        if b.h.ran: break
        time.sleep(0.05)
    assert b.h.heard == [32000] and b.h.ran == ["turn on the lights"]
    # JARVIS speaks: the voice tap mirrors the line + its WAV to the headset and keeps the PC speakers quiet
    wav = V.make_wav(b"\x01\x00" * 800, 24000)
    assert b.on_line("Lights on, sir.", wav) is True
    line = c.recv_until("line")
    au = c.recv()
    fr = V.parse_audio_frame(au)
    assert line["text"] == "Lights on, sir." and fr["kind"] == V.A_WAV and fr["data"] == wav and fr["id"] == line["id"]
    assert b.on_line("Windows voice line", None) is False             # no WAV -> the PC still speaks
    c.send({"t": "media", "op": "next"}); c.recv_until("toast")
    assert b.h.media_ops == ["next"]
    c.send({"t": "fly", "kind": "shadow"}); r = c.recv_until("fly")
    assert r["resp"]["kind"] == "shadow" and b.h.fly_calls == [("shadow", None)]
    c.send({"t": "media", "op": "format_c"}); c.send({"t": "ping", "ts": 5}); assert c.recv_until("pong")["ts"] == 5
    assert b.h.media_ops == ["next"]                                    # unknown ops do nothing
    assert b.status()["connected"] == ["lan"]
    c.s.close()


def test_websocket_refuses_bad_proof_bad_origin_and_serves_trust_page(live):
    b, port = live
    dev, secret = b.store.add("Quest")
    c = WSClient(port); ch = c.recv()
    c.send({"t": "auth", "dev": dev, "mac": V.auth_mac(os.urandom(32), ch["n"])})
    assert c.recv_until("auth_fail")["why"] == "bad proof"
    c = WSClient(port); ch = c.recv()
    c.send({"t": "ptt", "op": "start", "id": 1}); c.send({"t": "cmd", "text": "shut down"})
    time.sleep(0.3); assert b.h.ran == []                               # nothing runs before auth
    c = WSClient(port, origin="https://evil.example")
    assert b"403" in c.status
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    s = ctx.wrap_socket(socket.create_connection(("127.0.0.1", port)), server_hostname="x")
    s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"); page = b""
    while True:
        chunk = s.recv(4096)
        if not chunk: break
        page += chunk
    assert b"JARVIS trusts this headset" in page and b"jarvis-vr.sites.tab.bot" in page
    s = ctx.wrap_socket(socket.create_connection(("127.0.0.1", port)), server_hostname="x")
    s.sendall(b"GET /ping HTTP/1.1\r\nHost: x\r\nOrigin: https://jarvis-vr.sites.tab.bot\r\n\r\n"); resp = s.recv(4096)
    assert b"Access-Control-Allow-Origin: https://jarvis-vr.sites.tab.bot" in resp and b"Private-Network" in resp


def test_lan_only_binding_and_peers():
    assert V.lan_ip(connect=lambda: "192.168.1.20") == "192.168.1.20"
    assert V.lan_ip(connect=lambda: "10.0.0.5") == "10.0.0.5"
    assert V.lan_ip(connect=lambda: "8.8.8.8") is None and V.lan_ip(connect=lambda: "127.0.0.1") is None
    assert V.is_lan_peer("192.168.1.7") and V.is_lan_peer("172.16.4.4") and not V.is_lan_peer("8.8.8.8")


def test_cert_is_self_signed_for_the_lan_ip_and_reused(tmp_path):
    from cryptography import x509
    cp, kp = V.make_cert(str(tmp_path), "192.168.1.20")
    cert = x509.load_pem_x509_certificate(open(cp, "rb").read())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(i) for i in san.get_values_for_type(x509.IPAddress)] == ["192.168.1.20"]
    m = os.path.getmtime(cp)
    assert V.make_cert(str(tmp_path), "192.168.1.20") == (cp, kp) and os.path.getmtime(cp) == m
    V.make_cert(str(tmp_path), "192.168.1.21")
    assert "192.168.1.21" in open(tmp_path / "cert.json").read()


def test_confirm_flow_from_the_headset(tmp_path):
    b = bridge(tmp_path); s = Sess(b); s.authed = True
    ask = b.make_ask(s, timeout=3)
    out = {}
    t = threading.Thread(target=lambda: out.update(a=ask("Close Discord?"))); t.start()
    for _ in range(50):
        if s.out: break
        time.sleep(0.02)
    q = s.out[-1]
    assert q["t"] == "ask" and q["q"] == "Close Discord?" and b.h.said[-1] == "Close Discord?"
    b.on_json(s, {"t": "confirm", "id": q["id"], "answer": "yes"}); t.join(2)
    assert out["a"] == "yes"
    assert b.make_ask(s, timeout=0.1)("again?") == "no"                 # no answer = no


def test_ask_tab_mode_prefixes_the_words(tmp_path):
    b = bridge(tmp_path); s = Sess(b); s.authed = True
    b.on_json(s, {"t": "ptt", "op": "start", "id": 3, "mode": "tab"})
    b.on_binary(s, V.audio_frame(V.A_PCM16, 3, 0, b"\x10\x00" * 16000))
    b.on_json(s, {"t": "ptt", "op": "end", "id": 3})
    assert b.h.ran == ["ask Tab turn on the lights"]
    b.h.answer = "ask Tab what's the weather"; b.h.ran.clear()
    b.on_json(s, {"t": "ptt", "op": "start", "id": 4, "mode": "tab"}); b.on_binary(s, V.audio_frame(1, 4, 0, b"\x10\x00" * 16000)); b.on_json(s, {"t": "ptt", "op": "end", "id": 4})
    assert b.h.ran == ["ask Tab what's the weather"]
    b.h.ran.clear(); b.on_json(s, {"t": "ptt", "op": "start", "id": 5}); b.on_binary(s, V.audio_frame(1, 5, 0, b"\x00\x00" * 1000)); b.on_json(s, {"t": "ptt", "op": "end", "id": 5})
    assert b.h.ran == [] and s.out[-1]["why"] == "too short"


def test_unpair_drops_every_device(tmp_path):
    b = bridge(tmp_path); b.store.add("a"); b.store.add("b")
    s = Sess(b); s.authed = True; b.sessions.append(s)
    assert b.unpair() == 2 and not b.store.devices() and s.closed and s.out[-1] == {"t": "unpaired"}


# ================================================================ relay fallback
class FakeResp:
    def __init__(self, lines=None, data=b""):
        self.lines, self.data = lines or [], data
    def __iter__(self): return iter(self.lines)
    def read(self, n=-1): return self.data
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def relay_bridge(tmp_path):
    b = bridge(tmp_path, relay_batch_s=0.05)
    posts, files = [], {}
    b.relay.poster = lambda server, topic, body, message=None: posts.append((topic, body, message))
    b.relay.opener = lambda req, timeout=30: FakeResp(data=files[req.full_url])
    return b, posts, files


def test_relay_pairing_and_sealed_session(tmp_path):
    b, posts, files = relay_bridge(tmp_path)
    code, _ = b.start_pairing()
    d, pub, ck, msg = headset_pair(code)
    up, down = V.pair_topics(ck)
    assert up in b.relay.topics()
    out = b.relay.on_event({"event": "message", "topic": up, "message": json.dumps(msg)})
    assert out["t"] == "paired" and posts[-1][0] == down
    dev, secret = V.pair_finish(ck, d, pub, json.loads(posts[-1][1]))
    cmd, reply = V.device_topics(secret)
    assert cmd in b.relay.topics() and up not in b.relay.topics()
    hello = V.seal({"t": "hello", "ts": int(time.time()), "mid": "m1"}, secret)
    b.relay.on_event({"event": "message", "topic": cmd, "message": hello})
    got = [V.unseal(p[1].decode(), secret) for p in posts if p[0] == reply]
    assert got[0]["t"] == "auth_ok" and got[0]["relay"] and got[1]["t"] == "state"
    n = len(posts)
    assert b.relay.on_event({"event": "message", "topic": cmd, "message": hello}) is None and len(posts) == n   # replay
    old = V.seal({"t": "hello", "ts": int(time.time()) - 3600, "mid": "m2"}, secret)
    assert b.relay.on_event({"event": "message", "topic": cmd, "message": old}) is None                     # stale
    assert b.relay.on_event({"event": "message", "topic": cmd, "message": V.seal({"t": "hello", "ts": int(time.time()), "mid": "m3"}, os.urandom(32))}) is None


def test_relay_utterance_attachment_and_batched_reply(tmp_path):
    b, posts, files = relay_bridge(tmp_path)
    dev, secret = b.store.add("Quest")
    cmd, reply = V.device_topics(secret)
    pcm = struct.pack("<16000h", *([2000] * 16000))
    blob = V.seal_bytes(V.audio_frame(V.A_ULAW, 5, 0, V.ulaw_encode(pcm)), secret)
    files["https://ntfy.sh/file/abc.bin"] = blob
    tok = V.seal({"t": "utter", "mode": "cmd", "rate": 16000, "ts": int(time.time()), "mid": "u1"}, secret)
    b.relay.on_event({"event": "message", "topic": cmd, "message": tok, "attachment": {"url": "https://ntfy.sh/file/abc.bin"}})
    assert b.h.heard == [32000] and b.h.ran == ["turn on the lights"]
    heard = [V.unseal(p[1].decode(), secret) for p in posts if p[0] == reply and p[2] is None]
    assert heard[-1]["t"] == "heard" and heard[-1]["text"] == "turn on the lights"
    files["https://evil.example/x"] = blob                                        # attachments only from the relay host
    with pytest.raises(ValueError):
        b.relay._fetch("https://evil.example/x")
    # two spoken lines close together -> ONE relay message with one mu-law attachment
    n = len(posts)
    w = V.make_wav(b"\x00\x10" * 2400, 24000)
    b.on_line("Line one.", w); b.on_line("Line two.", w)
    time.sleep(0.3)
    new = posts[n:]
    assert len(new) == 1 and new[0][2] is not None
    meta = V.unseal(new[0][2], secret); fr = V.parse_audio_frame(V.unseal_bytes(new[0][1], secret))
    assert meta["t"] == "lines" and meta["text"] == ["Line one.", "Line two."] and fr["kind"] == V.A_ULAW and len(fr["data"]) == 2 * 1600


def test_relay_daily_budget(tmp_path):
    b, posts, files = relay_bridge(tmp_path); b.cfg["relay_daily_budget"] = 3
    sec = os.urandom(32)
    sent = [b.relay.post(sec, {"t": "state"}) for _ in range(5)]
    assert sent == [True, True, True, False, False]
    assert b.relay.post(sec, {"t": "err", "text": "x"}) is True                    # errors still go through


# ================================================================ the headset's JavaScript, run in node
@need_node
def test_js_pairing_against_the_python_pc(tmp_path):
    b = bridge(tmp_path); code, _ = b.start_pairing()
    st, ansf, resf = tmp_path / "st.json", tmp_path / "ans.json", tmp_path / "res.json"
    p = subprocess.Popen([NODE, NODE_JS, SITE, "pair_start"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    p.stdin.write(json.dumps({"code": code[:3] + " " + code[3:], "state": str(st), "answer": str(ansf), "result": str(resf)})); p.stdin.close()
    msg = json.loads(_read_json(p.stdout))
    ans = b.handle_pair(msg)
    assert ans["t"] == "paired"
    ansf.write_text(json.dumps(ans)); p.wait(30)
    r = json.loads(resf.read_text())
    assert r["dev"] == ans["dev"] and base64.b64decode(r["secret"]) == b.store.secret(ans["dev"])


def _read_json(f):
    buf = ""
    while True:
        ch = f.read(1)
        if not ch: break
        buf += ch
        try:
            json.loads(buf); return buf
        except ValueError:
            continue
    return buf


@need_node
def test_js_crypto_matches_python():
    sec = os.urandom(32)
    pcm = struct.pack("<4h", 0, 500, -500, 20000)
    out = node("crypto", {"secret": V.b64(sec), "nonce": "abc123", "token": V.seal({"t": "state", "x": "✓"}, sec),
                          "pyblob": V.b64(V.seal_bytes(V.audio_frame(V.A_ULAW, 9, 2, V.ulaw_encode(pcm)), sec))})
    assert out["auth"] == V.auth_mac(sec, "abc123")
    assert [out["topics"]["cmd"], out["topics"]["reply"]] == list(V.device_topics(sec))
    assert out["unsealed"] == {"t": "state", "x": "✓"} and out["tampered"] is None
    assert V.unseal(out["token"], sec) == {"t": "hello", "n": 42, "s": "héllo ✓"}
    fr = V.parse_audio_frame(V.unseal_bytes(base64.b64decode(out["blob"]), sec))
    assert fr["kind"] == 2 and fr["id"] == 77 and fr["seq"] == 3
    assert list(struct.unpack("<5h", V.ulaw_decode(fr["data"]))) == list(struct.unpack("<5h", V.ulaw_decode(V.ulaw_encode(struct.pack("<5h", 0, 1000, -1000, 32000, -32000)))))
    assert out["pyframe"]["id"] == 9 and out["pyframe"]["pcm"] == list(struct.unpack("<4h", V.ulaw_decode(V.ulaw_encode(pcm))))
    assert out["ck"] == V.code_key("123456").hex() and [out["ptopics"]["up"], out["ptopics"]["down"]] == list(V.pair_topics(V.code_key("123456")))


# ================================================================ JARVIS OS wiring (vrpair.py)
class FakeBrain:
    sir = "sir"
    def __init__(self):
        self.said, self.handled, self.launched, self.ask, self.gaming = [], [], [], None, False
    def say(self, t): self.said.append(t); return t
    def handle(self, t):
        self.handled.append((t, self.ask)); return "ok"
    def launch(self, argv): self.launched.append(argv); return 0, ""
    def is_gaming(self): return self.gaming


def wired(tmp_path, **cfg):
    b = FakeBrain()
    class Spk: tap = None
    spk = Spk()
    br = P.wire(b, {"vr": cfg}, stt=lambda a: "what time is it", speaker=spk, data_dir=str(tmp_path), start_threads=False)
    br.relay.start = lambda: None
    return b, br, spk


@pytest.mark.parametrize("said,want", [
    ("pair my headset", "pair"), ("Jarvis, pair my quest", "pair"), ("connect my vr headset", "pair"), ("pair my quest 3", "pair"),
    ("new headset code", "pair"), ("unpair my headset", "unpair"), ("forget my quest", "unpair"),
    ("is my headset connected", "status"), ("vr status", "status"), ("jarvis vr link", "link"), ("where's the jarvis vr app", "link"),
    ("pair my phone", None), ("switch to my headset", None), ("what time is it", None),
])
def test_vr_phrases(said, want):
    assert P.parse_voice(said) == want


@pytest.mark.parametrize("said,action", [
    ("pair my headset", "pair_headset"), ("unpair my headset", "unpair_headset"), ("is my headset connected", "headset_status"),
    ("jarvis vr link", "vr_link"), ("check my graphics card", "gpu_check"),
])
def test_brain_routes_vr_phrases(said, action):
    assert B.parse_os(said)[0] == action


def test_wire_sets_brain_vr_and_the_voice_tap(tmp_path):
    b, br, spk = wired(tmp_path)
    assert b.vr is br and spk.tap == br.on_line and not br.store.devices()
    assert br.server is None                                            # nothing listens until he pairs
    b2 = FakeBrain(); assert P.wire(b2, {"vr": {"enabled": False}}, data_dir=str(tmp_path)) is None and b2.vr is None


def test_pair_by_voice_gives_a_code_on_screen_and_listens_on_the_lan(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "lan_ip", lambda connect=None: "127.0.0.1")
    so = socket.socket(); so.bind(("127.0.0.1", 0)); port = so.getsockname()[1]; so.close()
    b, br, _ = wired(tmp_path, port=port)
    words = P.handle(b, "pair")
    m = re.search(r"code: (\d) (\d) (\d), (\d) (\d) (\d), sir", words)
    assert m and br.code.active() and br.code.code == "".join(m.groups())
    assert f"127.0.0.1:{port}" in words and "five minutes" in words
    assert br.server is not None and br.status()["lan"]
    note = b.launched[-1]
    assert note[0] == "notify-send" and br.code.code[:3] + " " + br.code.code[3:] in note[-1] and V.APP_URL in note[-1]
    assert "No headset is paired yet" in P.handle(b, "status")
    P._close_if_unused(br)                                              # code still live: keeps listening
    assert br.server is not None
    br.code.spend(); P._close_if_unused(br)                             # code gone, nobody paired: port closed again
    assert br.server is None
    assert "No headset was paired" in P.handle(b, "unpair")
    assert "jarvis-vr dot sites" in P.handle(b, "link")
    br.stop()


def test_pair_without_a_bridge_says_so():
    b = FakeBrain(); b.vr = None
    assert "isn't running" in P.handle(b, "pair") and "isn't running" in P.handle(b, "status")


def test_selftest_pairs_a_pretend_headset_over_real_tls_and_leaves_nothing(tmp_path):
    so = socket.socket(); so.bind(("127.0.0.1", 0)); port = so.getsockname()[1]; so.close()
    b, br, _ = wired(tmp_path, bind_ip="127.0.0.1", port=port)
    br.start_threads = True
    r = P.selftest(br)
    assert r["ok"], r
    assert any("wrong code refused" in s for s in r["steps"]) and any("same secret" in s for s in r["steps"])
    assert any("auth_ok" in s for s in r["steps"])
    assert not br.store.devices() and not br.code.active() and br.server is None
    assert b.said == []                                                 # the pretend headset is never announced
    st = P.status_json(br)
    assert st["running"] and st["paired"] == 0 and not st["lan"]
    br.store.add("real Quest"); br.start_lan()                          # a real headset paired: selftest keeps it and the port
    assert P.selftest(br)["ok"] and len(br.store.devices()) == 1 and br.server is not None
    br.stop()
    assert P.selftest(None) == {"ok": False, "steps": [], "why": "the VR bridge isn't running"}


def test_selftest_refuses_while_a_real_code_is_live(tmp_path):
    b, br, _ = wired(tmp_path, bind_ip="127.0.0.1", port=0)
    br.code.new()
    assert "real pairing code is live" in P.selftest(br)["why"] and br.code.active()


def test_os_hooks_run_routes_confirmations_to_the_headset(tmp_path):
    b = FakeBrain(); h = P.OSHooks(b, stt=lambda a: " lights on ")
    ask = lambda q: "yes"
    h.run("close discord", ask)
    assert b.handled == [("close discord", ask)] and b.ask is None       # restored afterwards
    assert h.transcribe(b"\x00\x00" * 160) == " lights on "
    h.quiet = True; h.say_pc("Headset paired, sir."); assert b.said == []
    h.quiet = False; h.say_pc("Headset paired, sir."); assert b.said == ["Headset paired, sir."]
    with pytest.raises(RuntimeError):
        P.OSHooks(b).transcribe(b"\x00\x00")
    b.gaming = True; assert h.in_game()
    f = h.fly("shadow"); assert f["source"] == "stylised" and f["drives"]["jump"] == 1 and "STYLISED" in f["honest"]


def test_os_hooks_media_now_playing_and_stats():
    calls = []
    def run(argv, timeout=3):
        calls.append(argv)
        if argv[:2] == ["playerctl", "metadata"]:
            return 0, "Playing\tSong\tBand\tAlbum\n"
        if argv[0] == "nvidia-smi":
            return 0, "41, 55, 2048, 8192\n"
        return 0, ""
    h = P.OSHooks(FakeBrain(), run=run, version="JARVIS OS 1.0.0-rc5.20261028")
    assert h.now_playing() == {"title": "Song", "playing": True, "artist": "Band", "album": "Album"}
    assert h.media("next") and calls[-1] == ["playerctl", "next"] and h.media("play_pause") and calls[-1] == ["playerctl", "play-pause"]
    assert h.media("rm -rf") is False
    st = h.stats()
    assert st["gpu"] == 41 and st["gpu_temp"] == 55 and st["vram_total"] == 8.0 and "cpu" in st and st["mode"] == "balanced"
    assert h.version() == "JARVIS OS 1.0.0-rc5.20261028"
    h2 = P.OSHooks(FakeBrain(), run=lambda a, timeout=3: (1, ""))
    assert h2.now_playing() == {} and h2.media("next") is False and "gpu" not in h2.stats()


def test_os_version_reads_the_image_label(tmp_path):
    p = tmp_path / "version"; p.write_text("JARVIS_OS_VERSION=1.0.0-rc5.20261028\n")
    assert P.os_version(str(p)) == "JARVIS OS 1.0.0-rc5.20261028" and P.os_version(str(tmp_path / "nope")) == "JARVIS OS"


def test_hub_buttons_are_phrases_jarvis_os_understands():
    """Every Hub button is a built-in JARVIS OS command (none falls through to the offline brain or Tab)."""
    from jarvisd import workshop
    from jarvisd.core import commands
    built_in = B.LOCAL_KINDS | {"qol", "volume", "media", "open_app", "weather", "time", "date", "lock", "help"}
    assert len(V.HUB) == 12
    for label, said in V.HUB:
        ok = B.parse_os(commands.normalize(said)) or workshop.parse(said) or \
             commands.parse(said, app_names=tuple(B.APPS)).kind in built_in
        assert ok, (label, said)


def test_device_secrets_are_owner_only(tmp_path):
    st = V.Store(str(tmp_path / "vr" / "vr.json")); st.add("Quest")
    assert oct(os.stat(tmp_path / "vr" / "vr.json").st_mode & 0o777) == "0o600"
    V.make_cert(str(tmp_path / "c"), "192.168.1.20")
    assert oct(os.stat(tmp_path / "c" / "key.pem").st_mode & 0o777) == "0o600"


def test_speaker_tap_sends_lines_to_the_headset_and_quiets_the_pc():
    from jarvisd import voice_out
    played = []
    spk = voice_out.Speaker({}, run=lambda argv, **k: played.append(argv) or types.SimpleNamespace(returncode=0), load_piper=lambda p: None)
    spk.piper = types.SimpleNamespace()
    spk.render = lambda text: b"RIFFwav"
    got = []
    spk.tap = lambda text, wav: got.append((text, wav)) or True          # a headset is connected over the LAN
    spk.speak("Lights on, sir.")
    assert got == [("Lights on, sir.", b"RIFFwav")] and played == []
    spk.tap = lambda text, wav: False                                    # nobody connected: the PC speaks
    spk.speak("Hello.")
    assert played and played[-1][0] in ("pw-play", "paplay", "aplay")
    spk.tap = lambda text, wav: 1 / 0                                    # a broken tap never makes JARVIS mute
    played.clear(); spk.speak("Still here.")
    assert played


# ================================================================ firewall: port 8771 for the home network only
FW = ROOT / "image" / "system_files" / "usr" / "lib" / "firewalld"

def test_firewalld_service_and_lan_ipset():
    svc = ET.parse(FW / "services" / "jarvis-vr.xml").getroot()
    assert [(p.get("protocol"), p.get("port")) for p in svc.findall("port")] == [("tcp", "8771")]
    ips = ET.parse(FW / "ipsets" / "jarvis-lan.xml").getroot()
    assert ips.get("type") == "hash:net"
    assert {o.get("name"): o.get("value") for o in ips.findall("option")}.get("family") == "inet"
    assert sorted(e.text for e in ips.findall("entry")) == ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    import ipaddress
    for e in ips.findall("entry"):
        assert ipaddress.ip_network(e.text).is_private


def test_build_opens_8771_only_to_the_lan():
    sh = (ROOT / "image" / "build_files" / "build.sh").read_text()
    assert "firewall-offline-cmd" in sh and "jarvis-vr" in sh
    assert 'source NOT ipset="jarvis-lan" port port="8771" protocol="tcp" reject' in sh
    assert 'source ipset="jarvis-lan" port port="8771" protocol="tcp" accept' in sh
    assert "--zone=home --add-service=jarvis-vr" in sh
    assert "--zone=public --add-service=jarvis-vr" not in sh and "--add-port=8771" not in sh
    assert "cryptography" in sh                                         # the bridge's certificate needs it in the venv



def test_unpair_closes_the_port_at_once_even_while_the_relay_is_streaming(tmp_path, monkeypatch):
    """rc5 VM run 40604156665: 'unpair my headset' left 8771 listening for 23 s, because closing the relay's streaming
    GET waited for ntfy's next keepalive. A local server that sends headers and then nothing stands in for ntfy."""
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); rport = srv.getsockname()[1]
    held = []
    def serve():
        c, _ = srv.accept(); held.append(c); c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nTransfer-Encoding: chunked\r\n\r\n")
        time.sleep(30)
    threading.Thread(target=serve, daemon=True).start()
    so = socket.socket(); so.bind(("127.0.0.1", 0)); port = so.getsockname()[1]; so.close()
    b, br, _ = wired(tmp_path, bind_ip="127.0.0.1", port=port, relay_servers=[f"http://127.0.0.1:{rport}"])
    br.start_threads = True
    del br.relay.start                                                  # the real relay thread this time
    br.store.add("Quest")
    br.start_lan(); br.relay.start()
    for _ in range(100):
        if br.relay.resp is not None: break
        time.sleep(0.05)
    assert br.relay.resp is not None                                    # the relay is inside its streaming read
    t0 = time.time()
    words = P.handle(b, "unpair")
    took = time.time() - t0
    assert "Headset unpaired" in words and took < 3, took
    probe = socket.socket()
    assert probe.connect_ex(("127.0.0.1", port)) != 0                   # nothing listens on the bridge port any more
    probe.close()
    br.stop(); srv.close()
