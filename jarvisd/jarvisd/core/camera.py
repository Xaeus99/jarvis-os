r"""Camera greeting: "Welcome back, sir" when the owner sits down after being away.

Everything here stays on this PC: frames are looked at in memory and thrown away.
The only thing saved is the optional "learn my face" set (a few small grey face crops
plus a model file) in the JARVIS data folder (Windows: %LOCALAPPDATA%\JARVIS-data\faces; JARVIS OS:
~/.local/state/jarvis/faces). Nothing is uploaded, ever.
Light on CPU: one small frame every couple of seconds, paused while gaming.
No camera? It stays asleep and checks again every few minutes.
"""
from __future__ import annotations
import glob, logging, os, shutil, threading, time

log = logging.getLogger("jarvis.camera")

_cascade = None
def _detector():
    global _cascade
    if _cascade is None:
        import cv2
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets",
                            "haarcascade_frontalface_default.xml")
        path = here if os.path.exists(here) else os.path.join(getattr(cv2.data, "haarcascades", ""),
                                                               "haarcascade_frontalface_default.xml")
        _cascade = cv2.CascadeClassifier(path)
    return _cascade

def detect_faces(img, min_size=48):
    """[(x, y, w, h)] faces in a BGR or grey image."""
    import cv2
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = _detector().detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=(min_size, min_size))
    return [tuple(int(v) for v in f) for f in faces], gray

def _crop(gray, box, size=160):
    import cv2
    x, y, w, h = box
    return cv2.resize(gray[y:y + h, x:x + w], (size, size))


class FaceBook:
    """The optional 'learn my face' data. Local files only."""
    def __init__(self, folder):
        self.folder = folder
        self.model = None
        self.load()

    @property
    def enrolled(self):
        return self.model is not None

    def load(self):
        path = os.path.join(self.folder, "owner_lbph.yml")
        self.model = None
        if os.path.exists(path):
            try:
                import cv2
                m = cv2.face.LBPHFaceRecognizer_create()
                m.read(path)
                self.model = m
            except Exception as e:
                log.info("face model not loaded (%r); greeting anyone", e)

    def train(self, crops):
        import cv2, numpy as np
        os.makedirs(self.folder, exist_ok=True)
        for f in glob.glob(os.path.join(self.folder, "owner_*.png")):
            os.remove(f)
        for i, c in enumerate(crops):
            cv2.imwrite(os.path.join(self.folder, f"owner_{i:02d}.png"), c)
        m = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)
        m.train(list(crops), np.zeros(len(crops), dtype=np.int32))
        m.write(os.path.join(self.folder, "owner_lbph.yml"))
        self.model = m

    def forget(self):
        shutil.rmtree(self.folder, ignore_errors=True)
        self.model = None

    def is_owner(self, crop, threshold):
        if self.model is None:
            return True
        label, dist = self.model.predict(crop)
        return label == 0 and dist <= threshold


class FaceWatcher(threading.Thread):
    def __init__(self, cfg, data_dir, on_arrive, is_gaming=lambda: False, notify=lambda m: None):
        super().__init__(name="camera", daemon=True)
        self.cfg = cfg or {}
        self.enabled = threading.Event()
        if self.cfg.get("enabled", True):
            self.enabled.set()
        self.on_arrive = on_arrive
        self.is_gaming = is_gaming
        self.notify = notify
        self.book = FaceBook(os.path.join(data_dir, "faces"))
        self.stop_ev = threading.Event()
        self.cap = None
        self.has_camera = None
        self.last_seen = None
        self.started = time.time()
        self.busy = threading.Lock()   # held during enrollment
        self.interval = max(1.0, float(self.cfg.get("interval_seconds", 2)))
        self.away = float(self.cfg.get("away_minutes", 10)) * 60
        self.recheck = float(self.cfg.get("recheck_minutes", 3)) * 60
        self.threshold = float(self.cfg.get("match_threshold", 75))
        self.pause_for = {p.lower() for p in self.cfg.get("pause_when_running", ["obs64.exe"] if os.name == "nt" else
                                                               ["obs", "discord", "vesktop", "zoom", "snapshot", "kamoso", "cheese"])}

    # -- camera handling --
    def _open(self):
        import cv2
        idx = int(self.cfg.get("index", 0))
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if not cap or not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        ok, _ = cap.read()
        if not ok:
            cap.release(); return None
        return cap

    def _release(self):
        if self.cap is not None:
            try: self.cap.release()
            except Exception: pass
            self.cap = None

    def _frame(self):
        ok, frame = self.cap.read()
        if not ok:
            self._release(); return None
        return frame

    def _paused_reason(self):
        if not self.enabled.is_set():
            return "off"
        if self.is_gaming():
            return "gaming"
        if self.pause_for:
            try:
                import psutil
                names = {(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])}
                if names & self.pause_for:
                    return "camera app open"
            except Exception:
                pass
        return None

    # -- main loop --
    def run(self):
        try:
            import cv2  # noqa: F401
        except Exception as e:
            log.warning("camera greeting off: OpenCV missing (%r)", e); return
        while not self.stop_ev.is_set():
            why = self._paused_reason()
            if why:
                self._release(); self.stop_ev.wait(5); continue
            if self.cap is None:
                with self.busy:
                    self.cap = self._open()
                if self.cap is None:
                    if self.has_camera is not False:
                        log.info("no camera found; checking again every %.0f min", self.recheck / 60)
                    self.has_camera = False
                    self.stop_ev.wait(self.recheck); continue
                if not self.has_camera:
                    log.info("camera found; face greeting active")
                self.has_camera = True
            with self.busy:
                frame = self._frame() if self.cap is not None else None
            if frame is not None:
                self.look(frame)
            self.stop_ev.wait(self.interval)
        self._release()

    def look(self, frame, now=None):
        """Process one frame. Returns 'greet', 'seen', 'stranger' or 'none'."""
        now = now or time.time()
        boxes, gray = detect_faces(frame)
        if not boxes:
            return "none"
        owner = any(self.book.is_owner(_crop(gray, b), self.threshold) for b in boxes)
        if not owner:
            return "stranger"
        greet = (self.last_seen is None and now - self.started > 20) or \
                (self.last_seen is not None and now - self.last_seen > self.away)
        self.last_seen = now
        if greet:
            log.info("welcome back (face seen after being away)")
            try: self.on_arrive()
            except Exception as e: log.error("greeting: %r", e)
            return "greet"
        return "seen"

    # -- "learn my face" --
    def enroll(self, shots=15, seconds=12, say=lambda t: None):
        """Take a few snapshots of the face in front of the camera. Returns (ok, message)."""
        try:
            import cv2  # noqa: F401
            cv2.face  # contrib module
        except Exception:
            return False, ("I'd need the face-recognition add-on for that, sir. Running update.bat again installs it." if os.name == "nt"
                          else "I'd need the face-recognition add-on for that, sir. It ships with the next JARVIS OS update.")
        with self.busy:
            cap = self.cap or self._open()
            if cap is None:
                return False, "I can't see a camera, sir."
            crops, end = [], time.time() + seconds
            while len(crops) < shots and time.time() < end:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.2); continue
                boxes, gray = detect_faces(frame, min_size=60)
                if len(boxes) == 1:
                    crops.append(_crop(gray, boxes[0]))
                    time.sleep(0.35)
                else:
                    time.sleep(0.15)
            if cap is not self.cap:
                cap.release()
        if len(crops) < 5:
            return False, "I couldn't get a clear look, sir. Face the camera in good light, alone, and try again."
        self.book.train(crops)
        self.last_seen = time.time()
        return True, f"Got it, sir. I'll know you from now on, and ignore anyone else. {len(crops)} snapshots, stored on this PC only."
