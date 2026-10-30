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
CGNAT_HELPER = "/usr/libexec/jarvis-os/vr-cgnat"     # root helper (pkexec): adds/removes 100.64.0.0/10 in ipset jarvis-lan
CFG_PATH = os.path.expanduser("~/.config/jarvis/config.json")

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


def _save_vr_cfg(key, value, path=None):
    """Read-modify-write one key of the vr section of the person's config.json."""
    path = path or CFG_PATH
    try:
        cfg = json.load(open(path))
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("vr", {})[key] = value
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, path)


def set_cgnat(br, on, run=None, cfg_path=None):
    """The CGNAT option (rc6): some routers / ISPs give the home network 100.64.x.x addresses (RFC 6598) instead of
    192.168.x.x / 10.x.x.x, and the bridge refuses those by default. on=True lets the headset reach port 8771 from
    100.64.0.0/10 too: the root helper adds that range to the firewall's jarvis-lan ipset (permanent + now), and the
    bridge counts such addresses as home-network ones. on=False undoes both; on=None only reports.
    -> {"ok", "cgnat", "firewall", "error"?}"""
    run = run or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=40))
    if on is None:
        try:
            p = run(["pkexec", CGNAT_HELPER, "status"])
            fw = (p.stdout or "").strip().splitlines()[-1:] or ["unknown"]
            fw = fw[0]
        except Exception as e:
            fw = f"unknown ({e})"
        return {"ok": True, "cgnat": bool(br and br.cgnat()), "firewall": fw}
    try:
        p = run(["pkexec", CGNAT_HELPER, "on" if on else "off"])
        rc, out, err = p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        rc, out, err = 127, "", str(e)
    if rc != 0:
        return {"ok": False, "cgnat": bool(br and br.cgnat()), "firewall": "unchanged",
                "error": (err or out or f"helper exit {rc}")[-300:]}
    if br is not None:
        br.cfg["allow_cgnat"] = bool(on)
        if br.server is not None and not br.cfg.get("bind_ip"):
            # the listening address may change (or stop counting as home): restart the LAN server on the right one
            br.stop_lan()
            br.start_lan()
    try:
        _save_vr_cfg("allow_cgnat", bool(on), cfg_path)
    except OSError as e:
        log.warning("could not save vr.allow_cgnat: %s", e)
    return {"ok": True, "cgnat": bool(on), "firewall": out.splitlines()[-1] if out else ("on" if on else "off")}


def cgnat_words(r, sir="sir"):
    if not r.get("ok"):
        return f"I couldn't change that, {sir}: {r.get('error') or 'the firewall helper failed'}."
    if r.get("cgnat"):
        return (f"Done, {sir}. Your headset can now reach me from 100.64 addresses too, for routers that use carrier-grade NAT. "
                f"Say pair my headset to pair.")
    return f"Done, {sir}. The headset bridge only accepts the usual home-network addresses again."


def status_json(br):
    if br is None:
        return {"ok": False, "running": False}
    st = br.status()
    return {"ok": True, "running": True, "paired": st["paired"], "connected": st["connected"], "lan": st["lan"],
            "address": br.address(), "port": st["port"], "code_active": st["code_active"], "cert_sha256": st["cert"],
            "error": st["error"], "cgnat": st.get("cgnat", False)}


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


# ------------------------------------------------------------------ rc7: the CGNAT option by voice
_CG = r"(?:cgnat|carrier[- ]grade (?:nat|network address translation)|100\.64(?:\.x\.x| addresses?| ip addresses?)?)"
_CG_WHAT = r"(?:the )?(?:vr |headset |quest )?" + _CG + r"(?: mode| option| support| setting)?"


def _cg_norm(t):
    t = (t or "").lower().strip()
    t = re.sub(r"[!?.,]+$", "", t)
    t = re.sub(r"^(?:(?:hey |ok |okay )?jarvis[, ]*)", "", t)
    t = re.sub(r"^(?:please |can you |could you |let's |lets |go ahead and )+", "", t).strip()
    t = re.sub(r"(?: please| for me| jarvis| now)+$", "", t).strip()
    # what speech-to-text makes of "CGNAT": "CG NAT", "C.G.N.A.T.", "c g nat", "see gee nat", "CG-NAT"
    t = re.sub(r"\b(?:c\.? ?g\.?[ -]?n\.? ?a\.? ?t\.?|c\.? ?g\.?[ -]?nat|see gee nat|cg-nat|cgn at)(?=\W|$)", "cgnat", t)
    t = re.sub(r"\b100 point 64\b|\b100\.64\.0\.0(?:/10)?\b", "100.64", t)
    return t


def parse_cgnat(text):
    """'on' | 'off' | 'status' | None for the VR CGNAT option by voice (rc7):
    "my router uses CGNAT", "turn on CGNAT mode", "turn off CGNAT", "is CGNAT on"."""
    t = _cg_norm(text)
    if "cgnat" not in t and "carrier" not in t and "100.64" not in t:
        return None
    F = lambda rx: re.fullmatch(rx, t)
    NET = r"(?:my |our |the )?(?:home )?(?:router|network|internet|isp|internet provider|wi-?fi|modem)"
    if F(NET + r" (?:doesn't|does not|don't|do not|isn't|is not) (?:use|have|on|behind|using) " + _CG + r"(?: anymore| any more)?|"
         r"(?:turn|switch|shut) off " + _CG_WHAT + r"|(?:disable|remove|undo|stop) " + _CG_WHAT + r"|" +
         _CG_WHAT + r" off|(?:i )?(?:don't|do not) (?:need|want) " + _CG_WHAT):
        return "off"
    if F(r"(?:is|are) " + _CG_WHAT + r" (?:on|enabled|active|allowed|turned on)|" + _CG_WHAT + r" status|"
         r"(?:what(?:'s| is) )?(?:the )?status of " + _CG_WHAT + r"|(?:is|are) (?:the )?(?:vr )?(?:bridge|headset) (?:allowing|accepting) " + _CG):
        return "status"
    if F(NET + r" (?:uses|is using|is on|has|is behind|gives (?:me |out )?|hands out) " + _CG + r"|"
         r"(?:i(?:'m| am) (?:on|behind)|we(?:'re| are) (?:on|behind)) " + _CG + r"|"
         r"(?:turn|switch) on " + _CG_WHAT + r"|(?:enable|allow|activate|use) " + _CG_WHAT + r"|" + _CG_WHAT + r" on|"
         r"(?:my )?(?:headset|quest)(?: is| has| gets| got)? (?:on )?(?:a )?100\.64(?: address| ip)?"):
        return "on"
    return None


def cgnat_question(sir="sir"):
    return (f"That lets your headset reach me from 100.64 addresses, which some routers use for the home network. Only the "
            f"headset port opens, and only to your home network. Turn it on, {sir}?")


def cgnat_status_words(r, sir="sir"):
    fw = str(r.get("firewall") or "")
    if "mixed" in fw:
        return (f"The CGNAT option is half set, {sir}: the firewall and the saved setting disagree. Say turn off CGNAT "
                f"to reset it, then turn it on again if you need it.")
    on = bool(r.get("cgnat")) or bool(re.search(r"\bfirewall: on\b|^on$", fw))
    if on:
        return f"The CGNAT option is on, {sir}: your headset can reach me from 100.64 addresses too."
    return f"The CGNAT option is off, {sir}. The headset bridge accepts the usual home-network addresses only."
