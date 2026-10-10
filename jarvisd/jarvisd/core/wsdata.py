"""v4 workspace data: system stats, GPU, now-playing, recent clips and their thumbnails.
JARVIS OS (Linux) version of the Windows wsdata.py: same API; GPU from nvidia-smi or amdgpu sysfs instead of
win32pdh, now-playing from MPRIS (playerctl) instead of WinRT, Linux clip folders.
Everything here runs on background threads and never touches the microphone or the network
(except the weather, which already lived in weather.py)."""
from __future__ import annotations
import glob, hashlib, logging, os, re, shutil, subprocess, threading, time

log = logging.getLogger("jarvis.wsdata")

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".m4v", ".ts")
IMAGE_EXT = (".png", ".jpg", ".jpeg")


# ---------------- system stats ----------------
class Stats:
    """Call sample() every second or two; returns a dict. Cheap (psutil + an occasional GPU read)."""
    def __init__(self):
        import psutil
        self.ps = psutil
        psutil.cpu_percent(None)
        self._net = psutil.net_io_counters()
        self._t = time.monotonic()
        self.gpu = Gpu()
        self.disk_root = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else os.path.expanduser("~")  # / is read-only on an atomic OS
        self.boot = psutil.boot_time()

    def sample(self):
        ps = self.ps
        out = {"cpu": ps.cpu_percent(None)}
        vm = ps.virtual_memory()
        out["ram"] = vm.percent; out["ram_used_gb"] = vm.used / 2**30; out["ram_total_gb"] = vm.total / 2**30
        try:
            du = shutil.disk_usage(self.disk_root)
            out["disk"] = 100.0 * du.used / du.total; out["disk_free_gb"] = du.free / 2**30
            out["disk_total_gb"] = du.total / 2**30; out["disk_name"] = (self.disk_root.rstrip("\\/") or "/") if os.name == "nt" else ""
        except OSError:
            out["disk"] = None
        try:
            n = ps.net_io_counters(); now = time.monotonic(); dt = max(0.2, now - self._t)
            out["net_down"] = max(0.0, (n.bytes_recv - self._net.bytes_recv) / dt)
            out["net_up"] = max(0.0, (n.bytes_sent - self._net.bytes_sent) / dt)
            self._net, self._t = n, now
        except Exception:
            out["net_down"] = out["net_up"] = None
        out.update(self.gpu.read())
        out["uptime"] = time.time() - self.boot
        try:
            b = ps.sensors_battery()
            if b is not None:
                out["battery"] = b.percent; out["plugged"] = b.power_plugged
        except Exception:
            pass
        return out


class Gpu:
    """GPU load on JARVIS OS (Linux): nvidia-smi for NVIDIA (load, temperature, VRAM, name); for AMD the amdgpu driver's
    own files in /sys/class/drm/card*/device (gpu_busy_percent, mem_info_vram_*, hwmon temp). Intel exposes no load
    figure there, so it shows nothing. Returns {} when nothing is available (the Windows build used win32pdh)."""
    def __init__(self, drm_root="/sys/class/drm", smi=None):
        self.smi = smi if smi is not None else shutil.which("nvidia-smi")
        self.drm_root = drm_root
        self.card = None if self.smi else self._find_card()
        self._last = {}; self._last_t = 0
        self.name = None

    def _find_card(self):
        try:
            for c in sorted(os.listdir(self.drm_root)):
                dev = os.path.join(self.drm_root, c, "device")
                if re.fullmatch(r"card\d+", c) and os.path.exists(os.path.join(dev, "gpu_busy_percent")):
                    return dev
        except OSError:
            pass
        return None

    @staticmethod
    def _num(path):
        try:
            with open(path) as f:
                return float(f.read().strip())
        except (OSError, ValueError):
            return None

    def read_sysfs(self):
        dev = self.card
        out = {}
        busy = self._num(os.path.join(dev, "gpu_busy_percent"))
        if busy is None:
            return {}
        out["gpu"] = busy
        used, total = self._num(os.path.join(dev, "mem_info_vram_used")), self._num(os.path.join(dev, "mem_info_vram_total"))
        if used is not None and total:
            out["vram_used_gb"] = used / 2**30; out["vram_total_gb"] = total / 2**30
        for t in sorted(glob.glob(os.path.join(dev, "hwmon", "hwmon*", "temp1_input"))):
            v = self._num(t)
            if v is not None:
                out["gpu_temp"] = v / 1000.0; break
        return out

    def read(self):
        if time.monotonic() - self._last_t < 2.5:
            return self._last
        self._last_t = time.monotonic()
        try:
            if self.smi:
                p = subprocess.run([self.smi, "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total,name",
                                    "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
                line = (p.stdout or "").strip().splitlines()[0]
                u, temp, mu, mt, name = [x.strip() for x in line.split(",", 4)]
                self._last = {"gpu": float(u), "gpu_temp": float(temp), "vram_used_gb": float(mu) / 1024,
                              "vram_total_gb": float(mt) / 1024, "gpu_name": name}
            elif self.card:
                self._last = self.read_sysfs()
            else:
                self._last = {}
        except Exception as e:
            log.info("GPU read failed (%r); GPU stats off", e)
            self.smi = None; self.card = None; self._last = {}
        return self._last


# ---------------- now playing ----------------
def parse_playerctl(out):
    """`playerctl metadata --format '{{status}}\t{{title}}\t{{artist}}\t{{album}}\t{{playerName}}\t{{position}}\t{{mpris:length}}'`
    -> the same dict the Windows build made from its media session."""
    parts = (out or "").rstrip("\n").split("\t")
    if len(parts) < 5 or not (parts[1] or parts[2]):
        return {}
    def secs(v):
        try: return int(v) / 1e6
        except (TypeError, ValueError): return None
    app = parts[4].split(".")[0]
    return {"title": parts[1], "artist": parts[2], "album": parts[3], "playing": parts[0] == "Playing",
            "app": {"spotify": "Spotify", "brave": "Browser", "chromium": "Browser", "firefox": "Browser"}.get(app, app.capitalize()),
            "position": secs(parts[5]) if len(parts) > 5 else None, "duration": secs(parts[6]) if len(parts) > 6 else None}

def now_playing(run=None):
    """MPRIS (Spotify, browsers, most Linux players) through playerctl."""
    fmt = "{{status}}\t{{title}}\t{{artist}}\t{{album}}\t{{playerName}}\t{{position}}\t{{mpris:length}}"
    try:
        if run:
            code, out = run(["playerctl", "metadata", "--format", fmt])
        else:
            p = subprocess.run(["playerctl", "metadata", "--format", fmt], capture_output=True, text=True, timeout=3)
            code, out = p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_playerctl(out) if code == 0 else {}


# ---------------- recent clips ----------------
def clip_folders(xcfg=None, home=None):
    """Where clips land on JARVIS OS: OBS's recording folder (when JARVIS could ask OBS), Videos, Videos/Captures,
    Videos/Replays, and the home folder's top level (OBS's own default when no folder is set)."""
    home = home or os.path.expanduser("~")
    videos = os.path.join(home, "Videos")
    try:
        p = subprocess.run(["xdg-user-dir", "VIDEOS"], capture_output=True, text=True, timeout=2)
        if p.returncode == 0 and p.stdout.strip().startswith("/") and home == os.path.expanduser("~"):
            videos = p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    folders = list((xcfg or {}).get("clip_folders", [])) + [os.path.join(videos, "Captures"), os.path.join(videos, "Replays"), videos]
    extra = (xcfg or {}).get("_obs_record_dir")
    if extra:
        folders.insert(0, extra)
    seen, out = set(), []
    for f in folders:
        f = os.path.expanduser(f)
        k = os.path.abspath(f)
        if k not in seen and os.path.isdir(f):
            seen.add(k); out.append(f)
    return out

def recent_clips(folders, limit=8, max_depth=2):
    """Newest videos (and Game Bar screenshots) first: [(path, mtime, size)]."""
    items = []
    for root in folders:
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, files in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for f in files:
                low = f.lower()
                if low.endswith(VIDEO_EXT) or (low.endswith(IMAGE_EXT) and "capture" in dirpath.lower()):
                    p = os.path.join(dirpath, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    items.append((p, st.st_mtime, st.st_size))
    uniq = {}
    for it in items:
        uniq[os.path.normcase(it[0])] = it
    return sorted(uniq.values(), key=lambda x: -x[1])[:limit]

def thumb_path(cache_dir, path, mtime):
    h = hashlib.sha1(f"{path}|{int(mtime)}".encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(cache_dir, h + ".jpg")

def make_thumb(path, mtime, cache_dir, width=320):
    """A JPEG thumbnail in cache_dir (reused next time). Videos use OpenCV (already installed for the camera)."""
    os.makedirs(cache_dir, exist_ok=True)
    out = thumb_path(cache_dir, path, mtime)
    if os.path.exists(out):
        return out
    low = path.lower()
    try:
        import cv2
        if low.endswith(IMAGE_EXT):
            import numpy as np
            data = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        else:
            cap = cv2.VideoCapture(path)
            n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            if n > 10:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * 0.3))
            ok, img = cap.read()
            cap.release()
            if not ok:
                return None
        if img is None:
            return None
        h, w = img.shape[:2]
        img = cv2.resize(img, (width, max(1, int(h * width / w))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok:
            buf.tofile(out)
            return out
    except Exception as e:
        log.info("thumbnail for %s failed: %r", os.path.basename(path), e)
    return None

def prune_thumbs(cache_dir, keep=60):
    try:
        files = sorted(glob.glob(os.path.join(cache_dir, "*.jpg")), key=os.path.getmtime, reverse=True)
        for f in files[keep:]:
            os.remove(f)
    except OSError:
        pass


def human_bytes(n):
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

def human_age(ts):
    age = time.time() - ts
    if age < 90: return "just now"
    if age < 3600: return f"{int(age // 60)} min ago"
    if age < 86400: return f"{int(age // 3600)} h ago"
    if age < 7 * 86400: return f"{int(age // 86400)} d ago"
    return time.strftime("%b %d", time.localtime(ts))


# ---------------- background poller ----------------
class Poller(threading.Thread):
    """Runs fn every `every` seconds while `active()` is true and hands the result to `emit`."""
    def __init__(self, name, fn, emit, every, active=lambda: True):
        super().__init__(name=name, daemon=True)
        self.fn, self.emit, self.every, self.active = fn, emit, every, active
        self.kick = threading.Event(); self.stop = threading.Event()

    def run(self):
        while not self.stop.is_set():
            if self.active():
                try:
                    self.emit(self.fn())
                except Exception as e:
                    log.info("%s poll failed: %r", self.name, e)
            self.kick.wait(self.every); self.kick.clear()
