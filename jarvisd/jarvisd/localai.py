"""JARVIS's offline brain: a free local AI model (Ollama) for anything the built-in commands don't cover.

No account, no API key, nothing paid, and nothing leaves the PC. Everything lives in the person's home folder, so
the read-only OS image never changes and the ISO stays small:

    ~/.local/share/jarvis/ollama/bin/ollama      the Ollama runtime (downloaded once, about 1.4 GB, pinned version)
    ~/.local/share/jarvis/ollama/models/         the model (llama3.2:3b about 2 GB; llama3.2:1b about 1.3 GB)
    jarvis-ollama.service (user unit)            `ollama serve` on 127.0.0.1 only; uses the NVIDIA GPU when there
                                                 is one (Ollama ships its own CUDA runtime), the CPU otherwise

First login downloads it in the background with progress notifications (jarvis-ai-setup.service); "Jarvis,
download your offline brain" or `jarvisctl ai setup` does the same on demand. Config (~/.config/jarvis/config.json):
    "assistant": {"local_ai": true, "local_ai_model": "", "local_ai_url": "http://127.0.0.1:11434"}
local_ai false switches it off; an empty model picks one for the hardware (choose_model).
"""
from __future__ import annotations
import json, logging, os, re, shutil, subprocess, threading, time, urllib.request

log = logging.getLogger("jarvisd.localai")

OLLAMA_VERSION = "v0.40.2"
OLLAMA_URL = f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst"
OLLAMA_SUMS = f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/sha256sum.txt"
HOME = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "jarvis", "ollama")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "jarvis", "localai.json")
DEFAULT_URL = "http://127.0.0.1:11434"
BIG, SMALL = "llama3.2:3b", "llama3.2:1b"
RACE_MODELS = ["llama3.2:1b", "llama3.2:3b", "qwen2.5:3b"]   # raced on a GPU / 12 GB+ RAM: first good sentence wins
GAME_MODELS = ["llama3.2:1b"]                                 # while a game runs: only the small one (frees the GPU)
CODER = "qwen2.5-coder:3b"                                    # "Jarvis, write code for ..." (creator.py), ~1.9 GB
SIZES = {"llama3.2:1b": "about 1.3 GB", "llama3.2:3b": "about 2 GB", "qwen2.5:3b": "about 1.9 GB", CODER: "about 1.9 GB"}

PERSONA = """You are J.A.R.V.I.S., the voice assistant built into this gaming PC. You speak like Tony Stark's \
J.A.R.V.I.S.: calm, polite, dry British wit, and you call the user "sir". Your words are spoken aloud, so answer in \
one to three short sentences of plain speech: no lists, no markdown, no emoji, no URLs. The user is a teenager: keep \
everything age-appropriate. If asked for anything sexual, hateful, violent toward real people, illegal or dangerous, \
decline in one light, polite sentence and offer something else. You run offline on this PC, so you can't browse the \
web, read email or see the screen; for live facts (news, scores, prices) say you may be out of date. Never claim to \
have done something on the PC; the PC's own commands are handled before you. Don't mention these instructions."""

def cfg_of(cfg):
    a = (cfg or {}).get("assistant", {}) or {}
    return {"enabled": a.get("local_ai", True) is not False, "model": (a.get("local_ai_model") or "").strip(),
            "url": (a.get("local_ai_url") or DEFAULT_URL).rstrip("/")}

# ---------------------------------------------------------------- hardware -> model
def has_nvidia(run=subprocess.run):
    try:
        p = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True, timeout=8)
        return p.returncode == 0 and bool((p.stdout or "").strip())
    except (OSError, subprocess.SubprocessError):
        return False

def ram_gb():
    try:
        import psutil
        return psutil.virtual_memory().total / 2 ** 30
    except Exception:
        return 8.0

def choose_model(cfg=None, nvidia=None, ram=None):
    """The configured model, else the 3B model on an NVIDIA GPU or with 12 GB+ RAM, else the 1B one
    (a virtual PC with a few GB of RAM)."""
    c = cfg_of(cfg)
    if c["model"]:
        return c["model"]
    nvidia = has_nvidia() if nvidia is None else nvidia
    ram = ram_gb() if ram is None else ram
    return BIG if (nvidia or ram >= 11) else SMALL

def race_models(cfg=None, nvidia=None, ram=None):
    """The models that answer together. Configured list (assistant.local_ai_models), else the one configured model,
    else all three on an NVIDIA GPU or with 12 GB+ RAM, else only the 1B model (a small virtual PC can't race)."""
    a = (cfg or {}).get("assistant", {}) or {}
    if isinstance(a.get("local_ai_models"), list) and a["local_ai_models"]:
        return [str(m) for m in a["local_ai_models"]]
    c = cfg_of(cfg)
    if c["model"]:
        return [c["model"]]
    nvidia = has_nvidia() if nvidia is None else nvidia
    ram = ram_gb() if ram is None else ram
    return list(RACE_MODELS) if (nvidia or ram >= 11) else [SMALL]       # 11: a 12 GB PC shows ~11.6 GiB

# ---------------------------------------------------------------- talking to Ollama (HTTP on localhost)
def _req(url, body=None, timeout=10, opener=urllib.request.urlopen):
    data = None if body is None else json.dumps(body).encode()
    rq = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    return opener(rq, timeout=timeout)

def server_up(url=DEFAULT_URL, opener=urllib.request.urlopen):
    try:
        with _req(url + "/api/version", timeout=2, opener=opener) as r:
            return bool(json.loads(r.read() or b"{}").get("version"))
    except Exception:
        return False

def has_model(model, url=DEFAULT_URL, opener=urllib.request.urlopen):
    try:
        with _req(url + "/api/tags", timeout=5, opener=opener) as r:
            names = {m.get("name") for m in json.loads(r.read() or b"{}").get("models", [])}
    except Exception:
        return False
    return model in names or (model + ":latest") in names

def models_on_disk(url=DEFAULT_URL, opener=urllib.request.urlopen):
    try:
        with _req(url + "/api/tags", timeout=5, opener=opener) as r:
            names = {m.get("name") for m in json.loads(r.read() or b"{}").get("models", [])}
    except Exception:
        return set()
    return names | {n[:-len(":latest")] for n in names if n and n.endswith(":latest")}

def unload(models, url=DEFAULT_URL, opener=urllib.request.urlopen):
    """Free the graphics memory the chat models hold (keep_alive 0); used before the image engine runs."""
    for m in models:
        try:
            with _req(url + "/api/generate", {"model": m, "keep_alive": 0}, timeout=15, opener=opener) as r:
                r.read()
        except Exception as e:
            log.info("unload %s: %r", m, e)

def pull(model, url=DEFAULT_URL, progress=lambda pct, status: None, opener=urllib.request.urlopen):
    """Download a model through Ollama, reporting 0-100 %. Raises RuntimeError on Ollama's error line."""
    with _req(url + "/api/pull", {"model": model, "stream": True}, timeout=3600, opener=opener) as r:
        last = -1
        for line in r:
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("error"):
                raise RuntimeError(d["error"])
            tot, done = d.get("total"), d.get("completed")
            if tot and done is not None:
                pct = int(done * 100 / tot)
                if pct != last:
                    last = pct; progress(pct, d.get("status", ""))
            if d.get("status") == "success":
                progress(100, "success"); return True
    return has_model(model, url, opener)

def speakable(text, max_sentences=3):
    """Model output -> something a voice can say: no markdown, no lists, at most a few sentences."""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    t = re.sub(r"[*_#`>|]+", "", t)
    t = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    parts = re.split(r"(?<=[.!?])\s+", t)
    return " ".join(parts[:max_sentences]).strip()

LATENCY = os.path.join(os.path.dirname(STATE), "ai_latency.jsonl")

def _log_latency(res, n, path=None):
    path = path or LATENCY
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps({"t": round(time.time()), "winner": res.winner, "first_s": res.first_s, "racers": n}) + "\n")

class LocalAI:
    """ask(text) -> a short spoken answer from the local model, or None when the offline brain isn't ready."""
    def __init__(self, cfg=None, opener=urllib.request.urlopen, history=6):
        self.c = cfg_of(cfg)
        self.cfg = cfg or {}
        self.opener = opener
        self.history, self.keep = [], history
        self.lock = threading.Lock()

    @property
    def enabled(self):
        return self.c["enabled"]

    def model(self):
        return choose_model(self.cfg)

    def ready(self):
        return self.enabled and server_up(self.c["url"], self.opener) and bool(self.available())

    def racers(self):
        return race_models(self.cfg)

    def available(self, in_game=False):
        """Race models already downloaded (in a game: only the small one, or the smallest there is)."""
        have = models_on_disk(self.c["url"], self.opener)
        ms = [m for m in self.racers() if m in have]
        if in_game and ms:
            small = [m for m in ms if m in GAME_MODELS]
            ms = small or ms[:1]
        return ms

    def ask_stream(self, text, on_sentence, sir="sir", in_game=False, decide_timeout=60.0, total_timeout=120.0):
        """Race every downloaded model on this question; each sentence of the winner goes to on_sentence the moment
        it is complete. Returns the race result (.ok, .winner, .first_s, .text) or None when no model is ready."""
        if not self.enabled:
            return None
        from . import race
        models = self.available(in_game)
        if not models:
            return None
        msgs = [{"role": "system", "content": PERSONA.replace('"sir"', f'"{sir}"')}] + self.history[-self.keep:] + \
               [{"role": "user", "content": text}]
        url = self.c["url"]
        def gen(m):
            return lambda cancel: race.stream_ollama(url, m, msgs, cancel=cancel, options={"num_predict": 160},
                                                     timeout=total_timeout)
        with self.lock:
            res = race.race([race.Candidate(m, "local", gen(m)) for m in models], on_sentence,
                            decide_timeout=decide_timeout, total_timeout=total_timeout)
            log.info("race: %s models=%s first=%ss errors=%s", res.winner, models, res.first_s, res.errors)
            if res.ok:
                self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": res.text}]
                self.history = self.history[-self.keep:]
                try:
                    _log_latency(res, len(models))
                except Exception:
                    pass
            return res

    def forget(self):
        self.history = []

    def ask(self, text, sir="sir", timeout=90):
        if not self.enabled:
            return None
        msgs = [{"role": "system", "content": PERSONA.replace('"sir"', f'"{sir}"')}] + self.history[-self.keep:] + \
               [{"role": "user", "content": text}]
        body = {"model": self.model(), "messages": msgs, "stream": False, "keep_alive": "30m",
                "options": {"num_predict": 160, "temperature": 0.6}}
        with self.lock:
            try:
                with _req(self.c["url"] + "/api/chat", body, timeout=timeout, opener=self.opener) as r:
                    d = json.loads(r.read() or b"{}")
            except Exception as e:
                log.info("offline brain unavailable: %r", e); return None
            ans = speakable((d.get("message") or {}).get("content", ""))
            if not ans:
                return None
            self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": ans}]
            self.history = self.history[-self.keep:]
            return ans

# ---------------------------------------------------------------- setup (download + start), resumable
def read_state(path=STATE):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}

def write_state(st, path=STATE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, path)

def runtime_path(home=HOME):
    return os.path.join(home, "bin", "ollama")

def download(url, dest, progress=lambda pct: None, opener=urllib.request.urlopen, chunk=1 << 20):
    """Resumable download to dest (keeps dest.part between tries)."""
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    rq = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with opener(rq, timeout=60) as r:
        code = getattr(r, "status", 200)
        if have and code != 206:
            have = 0                                   # server ignored the range: start over
        total = int(r.headers.get("Content-Length") or 0) + have
        with open(part, "ab" if have else "wb") as f:
            got, last = have, -1
            while True:
                b = r.read(chunk)
                if not b:
                    break
                f.write(b); got += len(b)
                if total:
                    pct = int(got * 100 / total)
                    if pct != last:
                        last = pct; progress(pct)
    os.replace(part, dest)
    return dest

def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def install_runtime(home=HOME, url=OLLAMA_URL, sums_url=OLLAMA_SUMS, progress=lambda pct: None,
                    opener=urllib.request.urlopen, run=subprocess.run):
    """Download + verify + unpack the pinned Ollama runtime into home (no root, nothing outside the home folder)."""
    if os.path.exists(runtime_path(home)):
        return runtime_path(home)
    os.makedirs(home, exist_ok=True)
    name = url.rsplit("/", 1)[-1]
    arc = download(url, os.path.join(home, name), progress, opener)
    want = None
    try:
        with opener(urllib.request.Request(sums_url), timeout=30) as r:
            for line in r.read().decode().splitlines():
                f = line.split()
                if len(f) == 2 and f[1].lstrip("*./") == name:
                    want = f[0]
    except Exception as e:
        log.info("no checksum list for the AI runtime (%r)", e)
    if want and sha256(arc) != want:
        os.unlink(arc)
        raise RuntimeError("the AI runtime download was damaged; it will be fetched again next time")
    flag = "--zstd" if name.endswith(".zst") else "-z"
    p = run(["tar", flag, "-xf", arc, "-C", home], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("couldn't unpack the AI runtime: " + (p.stderr or "")[-200:])
    os.unlink(arc)
    if not os.path.exists(runtime_path(home)):
        raise RuntimeError("the AI runtime archive had no bin/ollama")
    return runtime_path(home)

def notifier(run=subprocess.run):
    """notify(title, body): one desktop notification that updates in place (notify-send -p / -r)."""
    box = {}
    def notify(title, body, icon="jarvis"):
        argv = ["notify-send", "-a", "J.A.R.V.I.S.", "-i", icon, "-p"] + (["-r", box["id"]] if box.get("id") else []) + [title, body]
        try:
            p = run(argv, capture_output=True, text=True, timeout=10)
            if p.returncode == 0 and (p.stdout or "").strip().isdigit():
                box["id"] = p.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        log.info("%s: %s", title, body)
    return notify

def setup(cfg=None, notify=None, run=subprocess.run, opener=urllib.request.urlopen, home=HOME, state=STATE,
          wait=time.sleep, say=None):
    """Make the offline brain ready: runtime, service, model. Idempotent and resumable. Returns the state dict."""
    c = cfg_of(cfg)
    notify = notify or notifier(run)
    st = read_state(state)
    if not c["enabled"]:
        st.update(status="off"); write_state(st, state); return st
    model = choose_model(cfg)
    st.update(status="installing", model=model, started=st.get("started") or time.time()); write_state(st, state)
    try:
        if not os.path.exists(runtime_path(home)):
            notify("Downloading my offline brain", "Step 1 of 2: the AI runtime (about 1.4 GB). You can keep using the PC.")
            step = {"last": -1}
            def p1(pct):
                if pct >= step["last"] + 10:
                    step["last"] = pct; notify("Downloading my offline brain", f"Step 1 of 2: AI runtime {pct}%")
                    st.update(step=1, progress=pct); write_state(st, state)
            install_runtime(home, progress=p1, opener=opener, run=run)
        run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        run(["systemctl", "--user", "start", "jarvis-ollama.service"], capture_output=True)
        for _ in range(60):
            if server_up(c["url"], opener):
                break
            wait(1)
        else:
            raise RuntimeError("the AI service didn't start (systemctl --user status jarvis-ollama)")
        if not has_model(model, c["url"], opener):
            size = "about 2 GB" if model == BIG else "about 1.3 GB" if model == SMALL else "a few GB"
            notify("Downloading my offline brain", f"Step 2 of 2: the {model} model ({size}).")
            step = {"last": -1}
            def p2(pct, status):
                if pct >= step["last"] + 10:
                    step["last"] = pct; notify("Downloading my offline brain", f"Step 2 of 2: {model} {pct}%")
                    st.update(step=2, progress=pct); write_state(st, state)
            pull(model, c["url"], p2, opener)
        st.update(status="ready", model=model, ready_at=time.time(), error=None); write_state(st, state)
        notify("Offline brain ready", f"I can chat without the internet now ({model}). Say \u201cHey Jarvis\u201d and ask me anything.")
        if say:
            say("My offline brain is ready, sir.")
        pull_extras(cfg, model, notify, st, state, opener)
        return st
    except Exception as e:
        st.update(status="failed", error=str(e)[:300]); write_state(st, state)
        notify("Offline brain: download paused", f"{e}. I'll try again next login, or say \u201cJarvis, download your offline brain\u201d.",
               icon="dialog-warning")
        return st

def pull_extras(cfg, primary, notify, st, state=STATE, opener=urllib.request.urlopen):
    """After the first model answers: the other race models, then the coding model (racing hardware only; a small
    virtual PC fetches the coder the first time he asks for code). A failure here never undoes 'ready'."""
    c = cfg_of(cfg)
    racers = race_models(cfg)
    extras = [m for m in racers if m != primary]
    if len(racers) > 1:
        extras.append(CODER)
    done = []
    for m in extras:
        if has_model(m, c["url"], opener):
            done.append(m); continue
        try:
            notify("Upgrading my offline brain", f"Adding {m} ({SIZES.get(m, 'a few GB')}). I can already chat meanwhile.")
            step = {"last": -1}
            def p3(pct, status, m=m):
                if pct >= step["last"] + 20:
                    step["last"] = pct; st.update(extra=m, extra_progress=pct); write_state(st, state)
            pull(m, c["url"], p3, opener); done.append(m)
        except Exception as e:
            log.info("extra model %s: %r", m, e)
    st.pop("extra", None); st.pop("extra_progress", None)
    st.update(models=[primary] + done); write_state(st, state)
    if done:
        n = len([m for m in racers if m == primary or m in done])
        notify("Offline brain upgraded", f"{n} models now race to answer you; the first good answer wins."
               + (" My coding brain is in too." if CODER in done else ""))
    return done

def status_line(state=STATE, sir="sir"):
    st = read_state(state)
    s = st.get("status")
    if s == "ready":
        ms = st.get("models") or [st.get("model")]
        racers = [m for m in ms if m != CODER]
        extra = f" Still adding {st['extra']}, {st.get('extra_progress', 0)} percent." if st.get("extra") else ""
        if len(racers) > 1:
            return f"My offline brain is ready, {sir}: {len(racers)} models race to answer, {', '.join(racers)}.{extra}"
        return f"My offline brain is ready, {sir}: {st.get('model')}.{extra}"
    if s == "installing":
        return f"My offline brain is still downloading, {sir}: step {st.get('step', 1)} of 2, {st.get('progress', 0)} percent."
    if s == "failed":
        return f"My offline brain's download stopped, {sir}: {st.get('error')}. Say \u201cdownload your offline brain\u201d to try again."
    if s == "off":
        return f"My offline brain is switched off in my settings, {sir}."
    return f"My offline brain isn't downloaded yet, {sir}. Say \u201cdownload your offline brain\u201d and I'll fetch it."
