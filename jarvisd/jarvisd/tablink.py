"""The Tab connection on JARVIS OS: the same channel as Windows JARVIS v5.1, so nothing changes on Tab's side.

  out:  a phrase JARVIS can't handle locally goes to Tab as mail ("JARVIS CMD: <text> [id]"): direct SMTP to Tab's
        mailbox server first, then the HTTPS relay if the internet provider blocks port 25. Only the transcribed
        text leaves the PC, never audio.
  in:   Tab's replies and actions arrive sealed (HMAC-SHA256 stream + encrypt-then-MAC, core/replies.py) on a private
        topic that JARVIS polls. Anything not sealed with this PC's key is ignored; actions must be fresh and unseen.
  back: results of Tab's actions go back sealed, on the derived results topic and by mail ("JARVIS RESULT [id]").

The link settings (mailbox address, relay, reply topic and key) are private and live only in
~/.config/jarvis/config.json under "forwarding" and "replies", never in this repository. `jarvisctl link
--from-windows` copies them from the Windows JARVIS install on the Windows partition (read-only), so a dual-boot PC
is linked to the same Tab without retyping anything.
"""
from __future__ import annotations
import datetime, glob, json, logging, os, platform, re, smtplib, socket, ssl, subprocess, time, urllib.request
from email.message import EmailMessage

from .core import replies

log = logging.getLogger("jarvisd.tablink")
VERSION = "JARVIS OS"
try:
    VERSION = "JARVIS OS " + open("/usr/lib/jarvis-os/version").read().strip()
except OSError:
    pass

def new_id():
    import secrets
    return "j" + "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(4))

def host_label():
    return re.sub(r"[^a-z0-9-]+", "-", (platform.node() or "pc").lower()).strip("-") or "pc"

def subject(cfg, text, cid=None):
    tail = f" [{cid}]" if cid else ""
    s = cfg.get("subject_prefix", "JARVIS CMD: ") + text
    if len(s) + len(tail) > 180:
        s = s[:177 - len(tail)] + "..."
    return s + tail

def body(text, cid=None, context=None):
    now = datetime.datetime.now().astimezone()
    b = f"{text}\n\n-- spoken to J.A.R.V.I.S. ({VERSION}, Linux) on PC '{platform.node()}' at {now:%Y-%m-%d %H:%M:%S %Z}\n"
    if cid:
        b += f"-- command id {cid}. Replies and actions come back on this PC's sealed reply channel.\n"
    if context:
        b += "\n-- the conversation just before this (oldest first):\n" + "\n".join(f"   {c}" for c in context[-6:]) + "\n"
    return b

def via_smtp(cfg, msg, timeout=6):
    label = host_label()
    msg["From"] = f"{cfg.get('from_name', 'J.A.R.V.I.S.')} <jarvis@{label}.invalid>"
    msg["To"] = cfg["to"]
    last = "no smtp hosts"
    for host in cfg.get("smtp_hosts", []):
        try:
            with smtplib.SMTP(host, 25, timeout=timeout, local_hostname=label + ".local") as s:
                s.ehlo()
                if s.has_extn("starttls"):
                    s.starttls(context=ssl.create_default_context()); s.ehlo()
                if not s.send_message(msg):
                    return True, f"smtp:{host}"
        except (OSError, smtplib.SMTPException) as e:
            last = f"{host}: {e!r}"
            if isinstance(e, (socket.timeout, TimeoutError, ConnectionRefusedError)):
                break
    return False, last

def via_relay(cfg, fields, timeout=15, opener=urllib.request.urlopen):
    url = cfg.get("relay_url")
    if not url:
        return False, "no relay configured"
    fields = dict(fields); fields.update({"pc": platform.node(), "_captcha": "false", "_template": "basic"})
    ref = cfg.get("relay_referer", "")
    req = urllib.request.Request(url, data=json.dumps(fields).encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json", "Referer": ref, "Origin": ref.rstrip("/"),
        "User-Agent": "JARVIS-OS/1.0"})
    try:
        with opener(req, timeout=timeout) as r:
            ans = json.loads(r.read().decode("utf-8", "replace") or "{}")
        return str(ans.get("success")).lower() == "true", f"relay: {ans.get('message')}"
    except Exception as e:
        return False, f"relay: {e!r}"

class Forwarder:
    """send(text) -> (route, detail); route is 'email', 'relay' or None. Remembers when port 25 is blocked."""
    def __init__(self, cfg, state_path=None, smtp=via_smtp, relay=via_relay):
        self.cfg, self.state_path, self._smtp, self._relay = cfg or {}, state_path, smtp, relay
        self.state = {}
        if state_path and os.path.exists(state_path):
            try: self.state = json.load(open(state_path))
            except Exception: self.state = {}
        self.last_id = None

    @property
    def configured(self):
        return bool(self.cfg.get("to") and (self.cfg.get("smtp_hosts") or self.cfg.get("relay_url")))

    def _save(self):
        if self.state_path:
            try: json.dump(self.state, open(self.state_path, "w"))
            except OSError: pass

    def send(self, text, cid=None, context=None):
        cid = cid or new_id(); self.last_id = cid
        order = ["relay", "smtp"] if self.state.get("smtp_blocked") else ["smtp", "relay"]
        tries = []
        for route in order:
            if route == "smtp":
                msg = EmailMessage(); msg["Subject"] = subject(self.cfg, text, cid); msg.set_content(body(text, cid, context))
                ok, detail = self._smtp(self.cfg, msg)
            else:
                ok, detail = self._relay(self.cfg, {"_subject": subject(self.cfg, text, cid), "command": text, "id": cid,
                                                    "context": " | ".join(context or [])[-1500:]})
            tries.append(f"{route}: {detail}")
            if ok:
                if route == "smtp" and self.state.get("smtp_blocked"):
                    self.state["smtp_blocked"] = False; self._save()
                return ("email" if route == "smtp" else "relay"), detail
            if route == "smtp":
                self.state["smtp_blocked"] = True; self._save()
        return None, "; ".join(tries)

def result_subject(result):
    ok = sum(1 for r in result.get("results", []) if r.get("ok"))
    rid = result.get("re") or result.get("mid")
    return f"JARVIS RESULT [{rid}] {result.get('status')} {ok}/{len(result.get('results', []))} (act {result.get('mid')})"

def send_result(fcfg, rcfg, result, post=replies.post, smtp=via_smtp, relay=via_relay):
    """Seal the result with the reply key; send it on the results topic and by mail. Returns the routes that worked."""
    key, topic, server = rcfg.get("key"), rcfg.get("topic"), rcfg.get("server", "https://ntfy.sh")
    token = replies.seal(result, key); routes = []
    try:
        post(server, replies.result_topic(topic, key), token); routes.append("topic")
    except Exception as e:
        log.info("result topic: %r", e)
    subj = result_subject(result)
    msg = EmailMessage(); msg["Subject"] = subj
    wrapped = "\n".join(token[i:i + 76] for i in range(0, len(token), 76))
    msg.set_content(f"J.A.R.V.I.S. on '{platform.node()}' ({VERSION}) finished Tab's actions: {subj[len('JARVIS RESULT '):]}.\n"
                    f"The details are sealed with the reply key (only Tab can read them).\n\n"
                    f"-----BEGIN JARVIS SEALED RESULT-----\n{wrapped}\n-----END JARVIS SEALED RESULT-----\n")
    msg.add_attachment(token.encode(), maintype="application", subtype="octet-stream", filename=f"result-{result.get('mid')}.jrv")
    ok, _ = smtp(fcfg, msg)
    if not ok:
        ok, _ = relay(fcfg, {"_subject": subj, "result": token if len(token) < 60000 else "(too big; see the results topic)",
                             "id": result.get("re") or result.get("mid")})
    if ok:
        routes.append("mail")
    return "+".join(routes) or "none"

# ---------------------------------------------------------------- Tab's actions on Linux
SUPPORTED = ("say", "ping", "launch", "open_url", "media", "volume", "wait", "screenshot", "sysinfo", "focus")
ALIASES = {"focus_window": "focus", "speak": "say", "sleep": "wait", "screen": "screenshot", "app_volume": "volume"}

def sysinfo_text(what=None, windows=None, procs=None):
    """The "sysinfo" action, as Windows JARVIS answers it: active window, open windows (from KWin; a process list if
    KWin can't be asked), disk, CPU/RAM, uptime. `windows`/`procs` are injectable for tests."""
    import psutil
    from . import kwin
    want = what or ["window", "apps", "disk", "cpu", "uptime"]
    want = [want] if isinstance(want, str) else list(want)
    out, wins, why = [], None, ""
    if "window" in want or "apps" in want:
        try:
            wins = windows() if windows else kwin.list_windows()
        except kwin.KWinUnavailable as e:
            why = str(e)
    if "window" in want:
        act = next((w for w in (wins or []) if w.get("active")), None)
        out.append(f"Active window: {act['title']} ({act['app']})" if act else
                   "Active window: " + ("none" if wins is not None else f"(unknown: {why})"))
    if "apps" in want:
        if wins is not None:
            out.append("Open windows:")
            out += [f"  {w.get('app') or '?'}: {w.get('title')}" + (" [minimized]" if w.get("minimized") else "")
                    for w in wins[:60]]
        else:
            names = procs() if procs else sorted({(p.info.get("name") or "") for p in psutil.process_iter(["name"])} - {""})
            out.append(f"Running processes (no window list: {why}): " + ", ".join(names[:80]))
    if "disk" in want:
        out.append("Disks:")
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint.startswith(("/sysroot", "/boot", "/var/lib/containers")) or "/snap" in part.mountpoint:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
                out.append(f"  {part.mountpoint} {u.free / 2**30:.1f} GB free of {u.total / 2**30:.1f} GB ({u.percent:.0f}% used)")
            except Exception:
                pass
    if "cpu" in want:
        vm = psutil.virtual_memory()
        out.append(f"CPU {psutil.cpu_percent(interval=0.3):.0f}%, RAM {vm.percent:.0f}% used of {vm.total / 2**30:.1f} GB")
    if "uptime" in want:
        up = time.time() - psutil.boot_time()
        out.append(f"PC on for {int(up // 3600)} h {int(up % 3600 // 60)} min; {VERSION}")
    return "\n".join(out)

class ActionRunner:
    """Runs the action list of a sealed message. Only a safe subset exists on Linux so far (the same kinds Windows
    JARVIS runs without asking); anything else is reported back as not supported, never guessed. Keyboard/mouse
    control stays behind the "let Tab drive" grant and the kill switch (remote_control.py), not here.
    allowed() -> False (config "tab_actions": false) refuses every action."""
    def __init__(self, hands, say, allowed=lambda: True, sleep=time.sleep):
        self.hands, self.say, self.allowed, self.sleep = hands, say, allowed, sleep

    def _launch(self, target):
        if target.startswith(("https://", "http://")) or "|" in target:
            return self.hands.open_uri(target)
        name = re.sub(r"[^A-Za-z0-9._-]+", "", target)
        for argv in (["gtk-launch", name], ["gtk-launch", name.lower()], ["flatpak", "run", name]):
            if name and self.hands.run(argv)[0] == 0:
                return True
        return False

    def run(self, msg):
        out = {"mid": msg.get("mid"), "re": msg.get("re"), "results": [], "pc": platform.node(), "os": VERSION}
        if msg.get("rejected"):
            out.update(status="rejected", note=msg["rejected"]); return out
        if not self.allowed():
            out.update(status="refused", note="Tab's actions are switched off on this PC"); return out
        if msg.get("say"):
            self.say(str(msg["say"]))
        for a in (msg.get("actions") or [])[:20]:
            t = str(a.get("type", ""))
            r = {"type": t, "ok": False}
            t = ALIASES.get(t, t)
            try:
                if t not in SUPPORTED:
                    r["error"] = "not supported on JARVIS OS yet"
                elif t == "say":
                    self.say(str(a.get("text", ""))); r["ok"] = True
                elif t == "ping":
                    r.update(ok=True, note="pong")
                elif t == "wait":
                    self.sleep(min(30.0, float(a.get("seconds", 1)))); r["ok"] = True
                elif t == "launch":
                    r["ok"] = bool(self._launch(str(a.get("target", ""))))
                elif t == "open_url":
                    url = str(a.get("url", ""))
                    if url.startswith("https://"):
                        r["ok"] = bool(self.hands.open_uri(url))
                    else:
                        r["error"] = "only https links"
                elif t == "media":
                    op = {"play_pause": "play-pause", "playpause": "play-pause", "pause": "pause", "play": "play",
                          "next": "next", "previous": "previous", "prev": "previous", "stop": "stop"}.get(
                          str(a.get("key") or a.get("action") or "").lower().replace(" ", "_"))
                    r["ok"] = bool(op) and self.hands.run(["playerctl", op])[0] == 0
                elif t == "volume":
                    lvl = a.get("level"); mute = a.get("mute")
                    if a.get("app"):
                        r["ok"] = self.hands.app_volume(str(a["app"]), lvl, mute) > 0
                    else:
                        v, muted = self.hands.master(lvl, mute)
                        r.update(ok=True, note=f"volume {v}%" + (" (muted)" if muted else ""))
                elif t == "sysinfo":
                    r.update(ok=True, note=sysinfo_text(a.get("what")))
                elif t == "focus":
                    from . import kwin
                    title = kwin.focus(a.get("title"), a.get("process") or a.get("app"))
                    r.update(ok=True, note=f"focused: {title}")
                elif t == "screenshot":
                    r.update(ok=bool(self.hands.screenshot()), note="saved in Pictures/Screenshots")
            except Exception as e:
                r["error"] = repr(e)[:200]
            out["results"].append(r)
        n_ok = sum(1 for r in out["results"] if r["ok"])
        out["status"] = "ok" if n_ok == len(out["results"]) else ("partial" if n_ok else "failed")
        return out

# ---------------------------------------------------------------- link import from the Windows side
WINDOWS_GLOBS = ("Users/*/Desktop/**/config.json", "Users/*/Downloads/**/config.json", "Users/*/Documents/**/config.json",
                 "Users/*/OneDrive*/**/config.json", "Users/*/JARVIS*/**/config.json", "JARVIS*/**/config.json",
                 "Program Files/JARVIS*/**/config.json", "Users/*/AppData/Local/JARVIS*/**/config.json")

def find_windows_configs(root, max_depth=6):
    """JARVIS config.json files on a (read-only mounted) Windows partition, newest first."""
    hits = set()
    for g in WINDOWS_GLOBS:
        for p in glob.glob(os.path.join(root, g), recursive=True):
            if p[len(root):].count(os.sep) > max_depth + 2:
                continue
            try:
                d = json.load(open(p, encoding="utf-8-sig"))
            except Exception:
                continue
            if isinstance(d, dict) and (d.get("replies") or {}).get("key") and (d.get("forwarding") or {}).get("to"):
                hits.add(p)
    return sorted(hits, key=os.path.getmtime, reverse=True)

LINK_KEYS = {"forwarding": ("to", "smtp_hosts", "relay_url", "relay_referer", "subject_prefix"),
             "replies": ("enabled", "server", "topic", "key", "poll_seconds", "max_age_minutes", "action_max_age_seconds")}

def import_link(win_cfg_path, linux_cfg_path):
    """Copy only the Tab link settings into the Linux config (everything else in the Linux config is kept)."""
    return merge_link(json.load(open(win_cfg_path, encoding="utf-8-sig")), linux_cfg_path)

def merge_link(src, linux_cfg_path):
    """Merge the Tab link settings from a Windows-JARVIS-shaped dict (a config.json, or a link-code bundle)."""
    cfg = {}
    if os.path.exists(linux_cfg_path):
        cfg = json.load(open(linux_cfg_path, encoding="utf-8"))
    for sec, keys in LINK_KEYS.items():
        cfg.setdefault(sec, {})
        for k in keys:
            if k in (src.get(sec) or {}):
                cfg[sec][k] = src[sec][k]
    a = src.get("assistant") or {}
    if a.get("address_as"):
        cfg["address"] = a["address_as"]
    for k in ("piper_voice", "piper_speed"):
        if k in a:
            cfg.setdefault("assistant", {})[k] = a[k]
    b = src.get("briefing") or {}                 # the weather city (only its coordinates are ever sent anywhere)
    for k in ("city", "latitude", "longitude", "units"):
        if k in b:
            cfg.setdefault("briefing", {})[k] = b[k]
    os.makedirs(os.path.dirname(linux_cfg_path), exist_ok=True)
    tmp = linux_cfg_path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, linux_cfg_path)
    return cfg

def bitlocker_present(run=subprocess.run):
    """True when a BitLocker-encrypted partition is on this PC (its files can't be read from Linux without the key)."""
    try:
        out = run(["lsblk", "-n", "-o", "FSTYPE"], capture_output=True, text=True).stdout
    except Exception:
        return False
    return any(l.strip().lower() == "bitlocker" for l in (out or "").splitlines())

def mount_windows_ro(run=subprocess.run, interactive=True):
    """Mount the biggest NTFS partition read-only with udisks (no root needed). Returns the mount point or None.
    interactive=False (the automatic first-login link) never pops a password dialog: if mounting the internal
    drive needs one, it just doesn't mount, and `jarvisctl link --from-windows` can be run later by hand."""
    try:
        out = run(["lsblk", "-J", "-b", "-o", "PATH,FSTYPE,SIZE,MOUNTPOINT"], capture_output=True, text=True).stdout
        devs = []
        for top in json.loads(out).get("blockdevices", []):
            devs.append(top); devs.extend(top.get("children") or [])
    except Exception:
        return None
    ntfs = sorted((d for d in devs if (d.get("fstype") or "").lower() == "ntfs"), key=lambda d: -int(d.get("size") or 0))
    for d in ntfs:
        if d.get("mountpoint"):
            return d["mountpoint"]
        argv = ["udisksctl", "mount", "-b", d["path"], "-o", "ro"] + ([] if interactive else ["--no-user-interaction"])
        r = run(argv, capture_output=True, text=True)
        m = re.search(r" at (/\S+)", r.stdout or "")
        if m:
            return m.group(1).rstrip(".")
    return None


# ---------------------------------------------------------------- BitLocker: read Windows with the recovery key
UNLOCK = "/usr/libexec/jarvis-os/windows-ro-unlock"
_RKEY = re.compile(r"^\d{6}(-\d{6}){7}$")

def normalize_recovery_key(text):
    """'123456 123456 ...' / '123456-...' -> '123456-123456-...' or None. BitLocker recovery keys are 8 groups of 6 digits."""
    digits = re.sub(r"\D", "", text or "")
    if len(digits) != 48:
        return None
    key = "-".join(digits[i:i + 6] for i in range(0, 48, 6))
    return key if _RKEY.match(key) else None

def bitlocker_partitions(run=subprocess.run):
    """BitLocker partitions, biggest first (the biggest is C:)."""
    try:
        out = run(["lsblk", "-J", "-b", "-o", "PATH,FSTYPE,SIZE"], capture_output=True, text=True).stdout
        devs = []
        for top in json.loads(out).get("blockdevices", []):
            devs.append(top); devs.extend(top.get("children") or [])
    except Exception:
        return []
    return [d["path"] for d in sorted((d for d in devs if (d.get("fstype") or "").lower() == "bitlocker"),
                                      key=lambda d: -int(d.get("size") or 0))]

def unlock_windows_ro(dev, key, run=subprocess.run):
    """Open the BitLocker partition read-only (pkexec asks for this PC's password). Mount point or None.
    The key goes to the root helper on stdin only; it is never stored or logged."""
    r = run(["pkexec", UNLOCK, "open", dev], input=key + "\n", capture_output=True, text=True)
    if r.returncode != 0:
        log.warning("BitLocker unlock failed: %s", (r.stderr or "").strip()[-200:])
        return None
    lines = (r.stdout or "").strip().splitlines()
    return lines[-1] if lines else None

def lock_windows(run=subprocess.run):
    run(["pkexec", UNLOCK, "close"], capture_output=True, text=True)
