"""JARVIS makes things, locally and for $0 (shared design with JARVIS for Windows v5.3).

- CodeMaker: "Jarvis, write code for ..." -> a local coding model (Ollama qwen2.5-coder:3b, ~1.9 GB, downloaded the first
  time) writes ONE file into ~/Documents/JARVIS Code/ and the JARVIS viewer opens it. JARVIS never runs code it wrote.
- ImageMaker: "Jarvis, make an image of ..." -> stable-diffusion.cpp + SD-Turbo, 512x512. On an NVIDIA card it uses the
  Vulkan build (the NVIDIA driver's Vulkan, no CUDA install); without one, the CPU build (slow, a minute or two).
  The chat models are unloaded first so the image model fits in graphics memory, and it refuses while a game runs.
  Strict safety (he's a minor): prompt check + negative prompt + an NSFW classifier on every result; a blocked image is
  deleted and never shown or saved. Results go to ~/Pictures/JARVIS Images/.
Everything downloads on first use into ~/.local/share/jarvis/imagegen (never baked into the OS image).
"""
from __future__ import annotations
import json, logging, os, random, re, shutil, stat, subprocess, sys, threading, time, urllib.request, zipfile

from . import localai, safety

log = logging.getLogger("jarvisd.creator")

SD_TAG = "master-955-b7a74e6"
SD_BASE = f"https://github.com/leejet/stable-diffusion.cpp/releases/download/{SD_TAG}/"
SD_BUILDS = {   # name -> (zip, sha256, MB): Linux x86_64 builds of the same release the Windows version pins
    "vulkan": ("sd-master-b7a74e6-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip", "70a5bfcc65b241e7598523887b070bdb0a58f725a6b365b581be49a0e7301857", 37.0),
    "cpu": ("sd-master-b7a74e6-bin-Linux-Ubuntu-24.04-x86_64.zip", "c33577f8340dc1ac4212adac7def108ecc08e3047d8a3c2d460cc878eac0e78b", 26.1),
}
SD_MODEL = ("sd_turbo-f16-q8_0.gguf",
            "https://huggingface.co/Green-Sky/SD-Turbo-GGUF/resolve/19a31586d02d64a73b4419bc193b3ecfaf38e1f0/sd_turbo-f16-q8_0.gguf",
            "d50be7655f0a554cf8041c145d88b210bd5f3c545423119dee62ae08cae51580", 2023.7)
NSFW_MODEL = ("nsfw_vit_int8.onnx",
              "https://huggingface.co/onnx-community/nsfw_image_detection-ONNX/resolve/1ceb3c7fe1e9f3f2507e6df577437f23a9149fd5/onnx/model_int8.onnx",
              "bdc941491cd09ae75c5f40b038a5cd64482fa4e2b5297747323c5cda31303920", 87.3)
CODER = localai.CODER
CODER_GB = 1.9
IMAGE_GB = round((SD_BUILDS["vulkan"][2] + SD_MODEL[3] + NSFW_MODEL[3]) / 1000, 1)     # ~2.1
DATA = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "jarvis", "imagegen")

# ---------------------------------------------------------------- folders + downloads
def user_dir(which, run=subprocess.run):
    """~/Documents or ~/Pictures as the desktop knows them (xdg-user-dir), else the usual place."""
    try:
        p = run(["xdg-user-dir", which.upper()], capture_output=True, text=True, timeout=5)
        d = (p.stdout or "").strip()
        if p.returncode == 0 and d and d != os.path.expanduser("~"):
            return d
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.join(os.path.expanduser("~"), which.capitalize())

def slug(text, n=40):
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (s[:n].rstrip("_") or "jarvis")

def need_disk(path, gb):
    os.makedirs(path, exist_ok=True)
    free = shutil.disk_usage(path).free / 1e9
    if free < gb + 1.0:
        raise RuntimeError(f"not enough free space: {gb:.1f} GB needed, {free:.1f} GB free")

def fetch(url, dest, sha=None, progress=lambda pct: None, opener=urllib.request.urlopen):
    """Resumable download, then the published SHA-256 must match (a bad file is deleted)."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    localai.download(url, dest, progress, opener)
    if sha and localai.sha256(dest) != sha:
        os.remove(dest)
        raise RuntimeError(f"{os.path.basename(dest)} was damaged in the download; it will be fetched again next time")
    return dest

def open_viewer(kind, payload, run=subprocess.run):
    """The JARVIS viewer is its own little window process (like the workspace); a crash there never touches JARVIS."""
    argv = ["/usr/lib/jarvis-os/venv/bin/python", "-m", "jarvisd.viewer", kind, payload["path"]]
    if payload.get("prompt"):
        argv += ["--prompt", payload["prompt"]]
    unit = f"jarvis-viewer-{int(time.time() * 1000) % 10 ** 9}"
    try:
        r = run(["systemd-run", "--user", "--collect", f"--unit={unit}", "-p", "WorkingDirectory=/usr/lib/jarvis-os",
                 "-E", "QT_QPA_PLATFORM=xcb"] + argv, capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError) as e:
        log.info("systemd-run viewer: %r", e)
    try:
        env = dict(os.environ); env.setdefault("QT_QPA_PLATFORM", "xcb")
        subprocess.Popen([sys.executable] + argv[1:], env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return True
    except OSError as e:
        log.warning("viewer: %r", e); return False

# ---------------------------------------------------------------- code
EXT = {"python": "py", "py": "py", "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts", "html": "html", "css": "css",
       "batch": "bat", "bat": "bat", "powershell": "ps1", "ps1": "ps1", "c": "c", "cpp": "cpp", "c++": "cpp",
       "csharp": "cs", "c#": "cs", "cs": "cs", "java": "java", "lua": "lua", "luau": "lua", "rust": "rs", "go": "go", "json": "json",
       "sql": "sql", "bash": "sh", "sh": "sh", "shell": "sh", "gdscript": "gd", "kotlin": "kt", "swift": "swift", "php": "php",
       "ruby": "rb", "markdown": "md", "yaml": "yml", "text": "txt", "": "txt"}
LANG_HINT = [(r"\b(?:python|py)\b", "python"), (r"\bjava ?script|\bjs\b|\bnode\b", "javascript"), (r"\btype ?script\b", "typescript"),
             (r"\bhtml|web ?page|website\b", "html"), (r"\bcss\b", "css"), (r"\bbatch\b|\.bat\b", "batch"), (r"\bpower ?shell\b", "powershell"),
             (r"\bc\+\+|c plus plus|\bcpp\b", "cpp"), (r"\bc ?sharp|\bc#|unity\b", "csharp"), (r"\bjava\b", "java"),
             (r"\blua(?:u)?\b|roblox", "lua"), (r"\brust\b", "rust"), (r"\bgolang\b|\bgo program\b", "go"), (r"\bgd ?script|godot\b", "gdscript"),
             (r"\bsql\b", "sql"), (r"\bbash\b|\bshell script\b|\blinux script\b", "bash")]
BIG_BUILD = re.compile(r"\b(?:app|application|website|web ?site|web ?app|game|mod|discord bot|bot|full|entire|whole|complete|"
                       r"project|operating system|os|launcher|installer|extension|plugin|backend|server|multiplayer|deploy|"
                       r"publish|upload|github)\b")
CODE_SYSTEM = ("You are J.A.R.V.I.S.'s coding module. Write ONE complete, working, well-commented file for the request. "
               "Use {lang}. Output exactly one fenced code block (```{lang} ... ```) and then at most one short sentence "
               "saying how to use it. No placeholders like 'your code here'. Keep it under 250 lines. Never write code that "
               "deletes files without asking, steals data, spreads, hides itself, or attacks anything.")

def guess_lang(text):
    t = (text or "").lower()
    for rx, lang in LANG_HINT:
        if re.search(rx, t):
            return lang
    return "python"

def extract_code(reply, lang):
    """-> (code, language, note) from the model's markdown."""
    m = re.search(r"```([\w+#.-]*)[^\n]*\n(.*?)```", reply or "", re.S)
    if m:
        lg = (m.group(1) or lang).lower()
        return m.group(2).rstrip() + "\n", lg, (reply[m.end():].strip().split("\n")[0])[:200]
    m = re.search(r"```([\w+#.-]*)[^\n]*\n(.*)", reply or "", re.S)          # unterminated block
    if m:
        return m.group(2).rstrip() + "\n", (m.group(1) or lang).lower(), ""
    return (reply or "").strip() + "\n", lang, ""

def is_big_build(text):
    t = (text or "").lower()
    return bool(BIG_BUILD.search(t)) and not re.search(r"\b(?:script|function|snippet|one file|single file|small|simple|quick)\b", t)


class CodeMaker:
    def __init__(self, cfg=None, out_dir=None, model=CODER, show=None, opener=urllib.request.urlopen, timeout=600):
        self.c = localai.cfg_of(cfg)
        self.cfg = cfg or {}
        self.url = self.c["url"]
        self.out_dir = out_dir or os.path.join(user_dir("documents"), "JARVIS Code")
        self.model, self.opener, self.timeout = model, opener, timeout
        self.show = show if show is not None else open_viewer
        self.lock = threading.Lock()
        self.last = None

    def have_model(self):
        return localai.has_model(self.model, self.url, self.opener)

    def ensure_model(self, progress=lambda pct, status: None):
        """Pull the coding model (once, ~1.9 GB). Ollama resumes a partial pull by itself."""
        if self.have_model():
            return True
        need_disk(localai.HOME, CODER_GB)
        for i, wait in enumerate((0, 10, 30)):
            if wait:
                time.sleep(wait)
            try:
                if localai.pull(self.model, self.url, progress, self.opener):
                    return True
            except Exception as e:
                log.info("coder pull attempt %d: %r", i + 1, e)
        return False

    def free_vram(self):
        """Keep the small chat model; unload the bigger race models while the coder works."""
        localai.unload([m for m in localai.race_models(self.cfg) if m not in localai.GAME_MODELS], self.url, self.opener)

    def write(self, text):
        """Blocking: ask the coder, save, show. Returns (path, code, lang, note). The code is never executed."""
        lang = guess_lang(text)
        self.free_vram()
        body = {"model": self.model, "stream": False, "keep_alive": "3m",
                "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 3000},
                "messages": [{"role": "system", "content": CODE_SYSTEM.format(lang=lang)}, {"role": "user", "content": text}]}
        with localai._req(self.url + "/api/chat", body, timeout=self.timeout, opener=self.opener) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        code, lg, note = extract_code((d.get("message") or {}).get("content", ""), lang)
        if lg in ("", "text", "plaintext"):
            lg = lang
        os.makedirs(self.out_dir, exist_ok=True)
        name = slug(re.sub(r"^(?:jarvis,? )?(?:please )?(?:write|make|create|code|build|generate)(?: me)?(?: a| an| some)?"
                           r"(?: \w+)?(?: code| script| program| function)?(?: for| that| to| which)? ", "", text.lower()))
        ext = EXT.get(lg, "txt")
        path = os.path.join(self.out_dir, f"{name}.{ext}"); k = 2
        while os.path.exists(path):
            path = os.path.join(self.out_dir, f"{name}_{k}.{ext}"); k += 1
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(code)
        os.chmod(path, 0o644)                       # never executable: JARVIS doesn't run what it writes
        self.last = {"path": path, "code": code, "lang": lg, "note": note, "request": text}
        if self.show:
            self.show("code", {"path": path})
        return path, code, lg, note


# ---------------------------------------------------------------- images
class ImageMaker:
    def __init__(self, cfg=None, home=DATA, out_dir=None, show=None, run=subprocess.run, fetch=fetch, checker=None,
                 nvidia=None, opener=urllib.request.urlopen):
        self.cfg = cfg or {}
        ic = self.cfg.get("images", {}) or {}
        self.home = home
        self.out_dir = out_dir or os.path.join(user_dir("pictures"), "JARVIS Images")
        self.show = show if show is not None else open_viewer
        self.run, self.fetch, self.opener = run, fetch, opener
        self.url = localai.cfg_of(cfg)["url"]
        nv = localai.has_nvidia() if nvidia is None else nvidia
        self.build = ic.get("build") or ("vulkan" if nv else "cpu")
        self.size = int(ic.get("size", 512))
        self.steps = int(ic.get("steps", 4))
        self.cfg_scale = float(ic.get("cfg_scale", 1.5))     # >1 so the strict negative prompt is applied
        self.checker = checker or safety.NsfwCheck(os.path.join(self.home, NSFW_MODEL[0]), ic.get("nsfw_threshold", 0.2))
        self.lock = threading.Lock()
        self.last = None

    def exe(self, build=None):
        return os.path.join(self.home, build or self.build, "sd-cli")

    def model_path(self):
        return os.path.join(self.home, SD_MODEL[0])

    def installed(self, build=None):
        return os.path.isfile(self.exe(build)) and os.path.isfile(self.model_path()) and os.path.isfile(self.checker.path)

    def install(self, build=None, progress=lambda label, pct: None):
        """First use: engine (~30 MB) + SD-Turbo (~2 GB) + the safety model (~87 MB). Resumable, checksum-checked."""
        build = build or self.build
        zipname, zsha, _ = SD_BUILDS[build]
        need_disk(self.home, (0 if os.path.isfile(self.model_path()) else SD_MODEL[3] / 1000) + 0.2)
        steps = [(NSFW_MODEL[1], self.checker.path, NSFW_MODEL[2], "safety check"),
                 (SD_BASE + zipname, os.path.join(self.home, zipname), zsha, "image engine"),
                 (SD_MODEL[1], self.model_path(), SD_MODEL[2], "image model")]
        for url, dest, sha, label in steps:
            if dest.endswith(".zip"):
                if os.path.isfile(self.exe(build)):
                    continue
            elif os.path.isfile(dest):
                continue
            self.fetch(url, dest, sha=sha, progress=lambda pct, label=label: progress(label, pct))
            if dest.endswith(".zip"):
                self._unpack(dest, build)
        return True

    def _unpack(self, zpath, build):
        tgt = os.path.join(self.home, build); tmp = tgt + ".new"
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        found = [os.path.join(r, f) for r, _, fs in os.walk(tmp) for f in fs if f == "sd-cli"]
        if not found:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError("the image engine archive had no sd-cli")
        root = os.path.dirname(found[0])
        for r, _, fs in os.walk(root):           # zip files don't keep the executable bit
            for f in fs:
                p = os.path.join(r, f)
                if f == "sd-cli" or ".so" in f:
                    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        shutil.rmtree(tgt, ignore_errors=True); os.replace(root, tgt)
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            os.remove(zpath)
        except OSError:
            pass

    def free_vram(self):
        localai.unload(list(dict.fromkeys(localai.race_models(self.cfg) + localai.GAME_MODELS + [CODER])), self.url, self.opener)

    def argv(self, prompt, out, seed, build=None):
        b = build or self.build
        a = [self.exe(b), "-m", self.model_path(), "-p", prompt + safety.SAFE_SUFFIX, "-n", safety.NEGATIVE,
             "--steps", str(self.steps), "--cfg-scale", str(self.cfg_scale), "--sampling-method", "euler_a",
             "-W", str(self.size), "-H", str(self.size), "--seed", str(seed), "-o", out]
        if b == "cpu":
            a += ["--threads", str(max(2, (os.cpu_count() or 4) - 1))]
        return a

    def generate(self, prompt, seed=None, build=None):
        """Blocking. -> ("ok", path) | ("unsafe", None) | ("refused", kind) | ("failed", reason).
        An unsafe image is deleted before anyone sees it, and never saved."""
        ok, kind = safety.check_prompt(prompt)
        if not ok:
            return "refused", kind
        seed = seed if seed is not None else random.randint(1, 2 ** 31 - 1)
        tmpdir = os.path.join(self.home, "tmp"); os.makedirs(tmpdir, exist_ok=True)
        tmp = os.path.join(tmpdir, f"img_{int(time.time())}_{seed}.png")
        self.free_vram()
        b = build or self.build
        t0 = time.monotonic()
        try:
            env = dict(os.environ); env["LD_LIBRARY_PATH"] = os.path.dirname(self.exe(b)) + ":" + env.get("LD_LIBRARY_PATH", "")
            r = self.run(self.argv(prompt, tmp, seed, b), capture_output=True, text=True, timeout=900,
                         cwd=os.path.dirname(self.exe(b)), env=env)
            if getattr(r, "returncode", 1) != 0 or not os.path.isfile(tmp):
                raise RuntimeError(((getattr(r, "stderr", "") or "")[-300:]) or "no image written")
        except Exception as e:
            log.warning("sd-cli (%s) failed: %r", b, e)
            if b != "cpu" and (self.cfg.get("images", {}) or {}).get("cpu_fallback", True):
                try:
                    self.install("cpu")
                    return self.generate(prompt, seed, build="cpu")
                except Exception as e2:
                    log.warning("cpu fallback failed: %r", e2)
            return "failed", str(e)[:160]
        secs = time.monotonic() - t0
        safe, score = self.checker.is_safe(tmp)
        if not safe:
            try:
                os.remove(tmp)
            except OSError:
                pass
            log.warning("image blocked by the safety check (score %.2f); deleted", score)
            return "unsafe", None
        os.makedirs(self.out_dir, exist_ok=True)
        dest = os.path.join(self.out_dir, f"{slug(prompt)}_{time.strftime('%Y%m%d_%H%M%S')}.png")
        shutil.move(tmp, dest)
        self.last = {"path": dest, "prompt": prompt, "seed": seed, "seconds": round(secs, 1), "build": b, "nsfw": round(score, 3)}
        if self.show:
            self.show("image", {"path": dest, "prompt": prompt})
        return "ok", dest
