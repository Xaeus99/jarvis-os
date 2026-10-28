"""Quest headset pairing on JARVIS OS: wires the VR bridge (vrbridge.py, the same protocol as Windows JARVIS 7.1) into
jarvisd. Nothing listens on the network until he says "pair my headset" (or a headset was paired before: then the LAN
server and the relay start with JARVIS). Fails soft: if anything here breaks, JARVIS runs without VR.

Voice: "pair my headset" (also "pair my quest" / "connect my vr headset"), "unpair my headset", "is my headset
connected" / "VR status", "JARVIS VR link". jarvisctl: `jarvisctl vr status`, `jarvisctl vr selftest`.
"""
from __future__ import annotations
import base64, hashlib, json, logging, os, re, socket, ssl, subprocess, threading, time
from . import vrbridge as V

log = logging.getLogger("jarvisd.vrpair")

BRIDGE_READY = True
APP_URL = V.APP_URL
DATA = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "jarvis")
VERSION_FILE = "/usr/lib/jarvis-os/version"

_DEV = r"(?:my |the |a )?(?:vr |quest |meta quest |oculus )?(?:headset|quest(?: 3| 3s| 2| pro)?|vr|oculus)"


def parse_voice(text):
    """'pair' | 'unpair' | 'status' | 'link' | None. (Same phrases as Windows 7.1.)"""
    t = (text or "").lower().strip()
    t = re.sub(r"[!?.,]+$", "", t)
    t = re.sub(r"^(?:(?:hey |ok |okay )?jarvis[, ]*)", "", t)
    t = re.sub(r"^(?:please |can you |could you |let's |lets |go ahead and )+", "", t).strip()
    t = re.sub(r"(?: please| for me| jarvis| now)+$", "", t).strip()
    F = lambda rx: re.fullmatch(rx, t)
    if F(r"(?:un-?pair|unlink|forget) " + _DEV):
        return "unpair"
    if F(r"(?:pair|link|connect|hook up|set up) " + _DEV + r"(?: to (?:jarvis|you|the pc|my pc))?|(?:new|another) (?:headset|vr|quest) code|"
         r"vr pairing code|pair (?:with )?jarvis vr|pair (?:my |the |a )?(?:quest|vr|meta quest)? ?headset"):
        return "pair"
    if F(r"(?:is|are) " + _DEV + r" (?:connected|paired|online|on)|(?:vr|headset|quest) status|vr bridge status"):
        return "status"
    if F(r"(?:jarvis )?vr (?:app )?(?:link|address|url|website)|where(?:'s| is) (?:the )?(?:jarvis )?vr app|open (?:the )?jarvis vr(?: app)?"):
        return "link"
    return None


def os_version(path=VERSION_FILE):
    try:
        for line in open(path, encoding="utf-8"):
            if line.startswith("JARVIS_OS_VERSION="):
                return "JARVIS OS " + line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "JARVIS OS"


class OSHooks(V.Hooks):
    """The PC side for the bridge on Linux: the brain, faster-whisper, psutil / nvidia-smi, MPRIS (playerctl)."""
    def __init__(self, brain, stt=None, lock=None, cfg=None, run=None, version=None):
        self.b, self.stt, self.cfg = brain, stt, cfg or {}
        self.lock = lock or threading.Lock()          # the daemon's typed-command lock: VR never overlaps a typed command
        self.stt_lock = threading.Lock()
        self.run_cmd = run or self._run
        self._v = version or os_version()
        self.quiet = False                             # selftest: don't announce a pretend headset out loud
        self._gpu = (0.0, {})

    @staticmethod
    def _run(argv, timeout=3):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout or ""
        except (OSError, subprocess.SubprocessError):
            return 127, ""

    @property
    def sir(self):
        return getattr(self.b, "sir", "sir")

    def version(self):
        return self._v

    def transcribe(self, pcm16, rate=V.RATE):
        import numpy as np
        if self.stt is None:
            raise RuntimeError("speech recognition isn't loaded yet")
        audio = np.frombuffer(pcm16, dtype="<i2").astype(np.int16)
        with self.stt_lock:                            # one Whisper call at a time from VR
            return self.stt(audio)

    def run(self, text, ask):
        with self.lock:
            old = getattr(self.b, "ask", None)
            self.b.ask = ask                           # "are you sure?" goes to the headset
            try:
                return self.b.handle(text)
            finally:
                self.b.ask = old

    def stats(self):
        d = {}
        try:
            import psutil
            d["cpu"] = round(psutil.cpu_percent(interval=None), 1)
            m = psutil.virtual_memory()
            d.update(ram=round(m.percent, 1), ram_used=round(m.used / 2**30, 1), ram_total=round(m.total / 2**30, 1))
            try:
                temps = psutil.sensors_temperatures() or {}
                for k in ("k10temp", "coretemp", "zenpower", "cpu_thermal"):
                    if temps.get(k):
                        d["cpu_temp"] = round(max(t.current for t in temps[k]), 1); break
            except Exception:
                pass
        except Exception:
            pass
        now = time.time()
        if now - self._gpu[0] > 5:                     # nvidia-smi at most every 5 s
            g = {}
            rc, out = self.run_cmd(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                                    "--format=csv,noheader,nounits"])
            if rc == 0 and out.strip():
                try:
                    u, t, mu, mt = [float(x) for x in out.strip().splitlines()[0].split(",")]
                    g = {"gpu": round(u), "gpu_temp": round(t), "vram_used": round(mu / 1024, 1), "vram_total": round(mt / 1024, 1)}
                except ValueError:
                    g = {}
            self._gpu = (now, g)
        d.update(self._gpu[1])
        d["mode"] = "game" if self.in_game() else "balanced"
        d["time"] = time.strftime("%I:%M %p").lstrip("0")
        return d

    def now_playing(self):
        rc, out = self.run_cmd(["playerctl", "metadata", "--format", "{{status}}\t{{title}}\t{{artist}}\t{{album}}"])
        if rc != 0 or "\t" not in out:
            return {}
        status, title, artist, album = (out.rstrip("\n").split("\t") + ["", "", "", ""])[:4]
        if not title:
            return {}
        n = {"title": title, "playing": status.strip().lower() == "playing"}
        if artist: n["artist"] = artist
        if album: n["album"] = album
        return n

    def media(self, op):
        verb = {"play_pause": "play-pause", "next": "next", "previous": "previous"}.get(op)
        if not verb:
            return False
        return self.run_cmd(["playerctl", verb])[0] == 0

    def hub(self):
        extra = ((self.cfg.get("vr") or {}).get("hub") or [])
        out = [(str(x[0]), str(x[1])) for x in extra if isinstance(x, (list, tuple)) and len(x) == 2]
        return out or list(V.HUB)

    def fly(self, kind, side=None):
        """The fly brain simulation lives in Windows JARVIS; here the fly reacts with its stylised reflexes, labelled."""
        drives = {"food": {"fwd": 0.7, "turn": 0.6 if side != "l" else -0.6, "feed": 0.3}, "wind": {"groom": 1},
                  "shadow": {"jump": 1}, "light": {"fwd": 0.5, "turn": -0.5}}.get(kind, {})
        full = {k: 0 for k in ("fwd", "turn", "jump", "groom", "feed", "back")}; full.update(drives)
        return {"kind": kind, "source": "stylised", "drives": full, "trail": [],
                "honest": "STYLISED: a scripted reflex. The fly brain simulation runs in JARVIS on Windows, not on JARVIS OS."}

    def in_game(self):
        try:
            return bool(self.b.is_gaming())
        except Exception:
            return False

    def say_pc(self, text):
        if not self.quiet:
            self.b.say(text)

    def pc_name(self):
        return socket.gethostname()


def pair_words(code, addr, sir):
    spoken = " ".join(code[:3]) + ", " + " ".join(code[3:])
    where = f" Your PC is at {addr}." if addr else " I couldn't find a home-network address, so it will use the relay."
    return f"Headset pairing code: {spoken}, {sir}. Open JARVIS VR on the Quest and enter it within five minutes.{where}"


def wire(brain, cfg=None, stt=None, lock=None, speaker=None, data_dir=None, start_threads=True):
    """Create the bridge and hook it in: brain.vr, the voice tap (everything JARVIS says also goes to a connected
    headset). Returns the bridge, or None when VR is off in the config (vr.enabled false) or it failed."""
    cfg = cfg or {}
    vcfg = cfg.get("vr") or {}
    if vcfg.get("enabled", True) is False:
        log.info("JARVIS VR is off in config.json (vr.enabled)")
        brain.vr = None
        return None
    try:
        hooks = OSHooks(brain, stt=stt, lock=lock, cfg=cfg)
        br = V.Bridge(data_dir or DATA, hooks, vcfg, start_threads=start_threads)
    except Exception as e:
        log.warning("VR bridge unavailable: %r", e)
        brain.vr = None
        return None
    brain.vr = br
    if speaker is not None:
        speaker.tap = br.on_line
    if br.store.devices():                        # paired before: be reachable again (LAN + relay)
        if start_threads:
            threading.Thread(target=lambda: (br.start_lan(), br.relay.start()), daemon=True, name="vr-start").start()
    log.info("JARVIS VR bridge ready (%d paired)", len(br.store.devices()))
    return br


def _notify(brain, title, body):
    try:
        brain.launch(["notify-send", "--app-name=JARVIS", "--icon=jarvis", "--expire-time=300000", title, body])
    except Exception:
        pass


def _close_if_unused(br):
    if not br.store.devices() and not br.code.active() and not br.connected():
        br.stop_lan()


def pair(brain, timer=True):
    br = getattr(brain, "vr", None)
    s = brain.sir
    if br is None:
        return (f"The headset bridge isn't running, {s}. It's switched off in my settings or it failed to start; "
                f"restarting me with jarvisctl restart usually brings it back.")
    code, addr = br.start_pairing()
    _notify(brain, "Pair your VR headset",
            f"Code: {code[:3]} {code[3:]}  (5 minutes, one use)\nPC: {addr or 'no home-network address: relay only'}\n"
            f"App: {APP_URL}\nFirst time on this headset: tap 'Trust my PC' in the app and accept the warning once.")
    if timer and br.start_threads:
        t = threading.Timer(V.CODE_TTL + 2, _close_if_unused, args=(br,)); t.daemon = True; t.start()
    return pair_words(code, addr, s)


def unpair(brain):
    br = getattr(brain, "vr", None); s = brain.sir
    if br is not None:
        br.stop_lan()                             # port 8771 closes first, before anything that talks to the relay
    n = br.unpair() if br is not None else 0
    return f"Headset unpaired, {s}. It can't reach me any more." if n else f"No headset was paired, {s}."


def status_words(brain):
    br = getattr(brain, "vr", None); s = brain.sir
    if br is None:
        return f"The headset bridge isn't running, {s}."
    st = br.status()
    if not st["paired"]:
        return f"No headset is paired yet, {s}. Say pair my headset."
    if st["connected"]:
        how = "over your home network" if "lan" in st["connected"] else "through the relay"
        return f"Your headset is connected {how}, {s}."
    return f"{st['paired']} headset{'s' if st['paired'] > 1 else ''} paired, none connected right now, {s}."


def link_words(brain):
    _notify(brain, "JARVIS VR", f"{APP_URL}\nOpen it in the Quest browser, then Install / Add to library.")
    return f"JARVIS VR lives at jarvis-vr dot sites dot tab dot bot, {brain.sir}. It's in your notifications."


def handle(brain, action):
    """brain.os_action for the VR phrases. -> the words to say."""
    if action == "pair":
        return pair(brain)
    if action == "unpair":
        return unpair(brain)
    if action == "status":
        return status_words(brain)
    return link_words(brain)


def pair_line(sir="sir", brain=None):
    """Kept for older callers: the pairing words (or why there's no bridge)."""
    if brain is None:
        return f"The headset bridge isn't running, {sir}."
    return pair(brain)


def status_json(br):
    if br is None:
        return {"ok": False, "running": False}
    st = br.status()
    return {"ok": True, "running": True, "paired": st["paired"], "connected": st["connected"], "lan": st["lan"],
            "address": br.address(), "port": st["port"], "code_active": st["code_active"], "cert_sha256": st["cert"],
            "error": st["error"]}


# ------------------------------------------------------------------ selftest: a pretend headset over the real server
class _WS:
    """A minimal WebSocket client over TLS (what the Quest browser does), for the selftest and the tests."""
    def __init__(self, host, port, origin=V.ORIGINS[0], timeout=10):
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((host, port), timeout=timeout)
        self.s = ctx.wrap_socket(raw, server_hostname=host)
        self.cert_sha256 = hashlib.sha256(self.s.getpeercert(binary_form=True)).hexdigest()
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET / HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                        f"Sec-WebSocket-Version: 13\r\nOrigin: {origin}\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            c = self.s.recv(1)
            if not c:
                break
            resp += c
        self.status = resp.split(b"\r\n", 1)[0].decode("latin-1")
        if " 101 " not in self.status:
            raise ConnectionError(self.status or "no answer")
        if V.accept_key(key).encode() not in resp:
            raise ConnectionError("bad Sec-WebSocket-Accept")

    def send(self, obj):
        self.s.sendall(V.mask_frame(json.dumps(obj)))

    def recv(self, want=None, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            self.s.settimeout(max(0.1, end - time.time()))
            b1, b2 = V._recv_exact(self.s, 2); n = b2 & 0x7F       # server frames are unmasked
            if n == 126: n = int.from_bytes(V._recv_exact(self.s, 2), "big")
            elif n == 127: n = int.from_bytes(V._recv_exact(self.s, 8), "big")
            op, data = b1 & 0x0F, V._recv_exact(self.s, n)
            if op == 1:
                m = json.loads(data.decode("utf-8"))
                if want is None or m.get("t") in want:
                    return m
        raise TimeoutError(f"no {want} message")

    def close(self):
        try: self.s.close()
        except Exception: pass


def selftest(br, timeout=10):
    """A pretend headset pairs with this PC over the real wss server on the LAN address: wrong code refused (tries
    left 4), right code pairs (both sides hold the same secret), a fresh connection proves the secret and gets
    auth_ok + PC state, ping/pong. Leaves nothing behind: the test device is removed, the code spent, and the LAN
    server stopped again if no real headset is paired. -> {"ok": bool, "steps": [...], "why": ...}"""
    out = {"ok": False, "steps": []}
    step = lambda name: out["steps"].append(name)
    if br is None:
        out["why"] = "the VR bridge isn't running"; return out
    if br.code.active():
        out["why"] = "a real pairing code is live; try again in five minutes"; return out
    before = set(br.store.devices())
    had_server = br.server is not None
    hooks = br.h; old_quiet = getattr(hooks, "quiet", False); hooks.quiet = True
    ws = ws2 = None
    try:
        if not br.start_lan():
            out["why"] = br.last_error or "the LAN server didn't start"; return out
        step(f"listening on wss://{br.address()}")
        code = br.code.new()                      # no relay listener: this test stays on the LAN
        ws = _WS(br.ip, br.port, timeout=timeout)
        fp = V.cert_fingerprint(br.data)
        if fp and ws.cert_sha256 != fp:
            out["why"] = "the certificate the server presented isn't the one on disk"; return out
        step("TLS ok, self-signed certificate for " + br.ip)
        ch = ws.recv({"challenge"}, timeout)
        step("challenge received")
        wrong = "%06d" % ((int(code) + 1) % 1_000_000)
        d, pub = V.ec_keypair(); ckw = V.code_key(wrong)
        ws.send({"t": "pair", "pub": V.b64(pub), "mac": V.b64(V.pair_request_mac(ckw, pub)), "name": "selftest"})
        r = ws.recv({"pair_fail", "paired"}, timeout)
        if r.get("t") != "pair_fail" or r.get("left") != V.CODE_TRIES - 1:
            out["why"] = f"a wrong code was not refused properly: {r}"; return out
        step("wrong code refused (4 tries left)")
        ck = V.code_key(code)
        ws.send({"t": "pair", "pub": V.b64(pub), "mac": V.b64(V.pair_request_mac(ck, pub)), "name": "selftest"})
        r = ws.recv({"pair_fail", "paired"}, timeout)
        got = V.pair_finish(ck, d, pub, r) if r.get("t") == "paired" else None
        if not got or br.store.secret(got[0]) != got[1]:
            out["why"] = f"pairing failed: {r.get('why') or r.get('t')}"; return out
        dev, secret = got
        step("paired: both sides hold the same secret")
        if br.code.active():
            out["why"] = "the code still works after one use"; return out
        step("code used up")
        ws.close(); ws = None
        ws2 = _WS(br.ip, br.port, timeout=timeout)
        ch = ws2.recv({"challenge"}, timeout)
        ws2.send({"t": "auth", "dev": dev, "mac": V.auth_mac(secret, ch["n"])})
        r = ws2.recv({"auth_ok", "auth_fail"}, timeout)
        if r.get("t") != "auth_ok":
            out["why"] = f"the paired headset was refused: {r.get('why')}"; return out
        st = ws2.recv({"state"}, timeout)
        step("reconnected with the device secret (auth_ok, PC state with %d hub buttons)" % len(st.get("hub") or []))
        ws2.send({"t": "ping", "ts": 7})
        if ws2.recv({"pong"}, timeout).get("ts") != 7:
            out["why"] = "no pong"; return out
        step("ping/pong")
        out["ok"] = True
        return out
    except Exception as e:
        out["why"] = f"{type(e).__name__}: {e}"
        return out
    finally:
        for w in (ws, ws2):
            if w is not None: w.close()
        new = set(br.store.devices()) - before
        for dv in new:
            br.store.d["devices"].pop(dv, None)
        if new:
            br.store.save()
        for sess in br.connected():
            if sess.dev in new:
                sess.closed = True
        br.code.spend()
        try: br.relay.reload()
        except Exception: pass
        hooks.quiet = old_quiet
        if not had_server and not before:
            br.stop_lan()
