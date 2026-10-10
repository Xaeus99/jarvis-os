import datetime, os, threading
import numpy as np
import pytest
from jarvisd import camera as glue

cv2 = pytest.importorskip("cv2")
from jarvisd.core import camera as core

def test_greeting_line():
    now = datetime.datetime(2026, 10, 10, 19, 5)
    assert glue.greeting("sir", {}, lambda: "Clear skies, 12 degrees.", now) == \
        "Welcome back, sir. Good evening. It's 7:05 PM. Clear skies, 12 degrees."
    assert glue.greeting("sir", {"greet_with_time": False}, lambda: "I couldn't reach the weather service", now) == \
        "Welcome back, sir. Good evening."

def test_face_book_trains_and_forgets(tmp_path):
    rng = np.random.default_rng(1)
    me = [np.clip(rng.normal(120, 20, (160, 160)), 0, 255).astype(np.uint8) for _ in range(6)]
    book = core.FaceBook(str(tmp_path / "faces"))
    assert not book.enrolled and book.is_owner(me[0], 75)          # not enrolled: greets anyone
    book.train(me)
    assert book.enrolled and core.FaceBook(str(tmp_path / "faces")).enrolled   # survives a restart
    book.forget()
    assert not book.enrolled and not os.path.exists(tmp_path / "faces")

def test_look_greets_after_being_away(tmp_path, monkeypatch):
    said = []
    w = core.FaceWatcher({"away_minutes": 10}, str(tmp_path), lambda: said.append("hi"))
    t0 = 1_000_000.0
    w.started = t0 - 60
    monkeypatch.setattr(core, "detect_faces", lambda f, min_size=48: ([(0, 0, 50, 50)], f))
    frame = np.zeros((240, 320), np.uint8)
    assert w.look(frame, now=t0) == "greet"
    assert w.look(frame, now=t0 + 60) == "seen"
    assert w.look(frame, now=t0 + 60 + 11 * 60) == "greet" and said == ["hi", "hi"]
    monkeypatch.setattr(core, "detect_faces", lambda f, min_size=48: ([], f))
    assert w.look(frame, now=t0 + 2000) == "none"

def test_detector_loads_from_opencv_data():
    faces, gray = core.detect_faces(np.zeros((240, 320, 3), np.uint8))
    assert faces == [] and gray.shape == (240, 320)

def test_paused_in_games_and_for_webcam_apps(tmp_path):
    gaming = {"on": False}
    w = core.FaceWatcher({}, str(tmp_path), lambda: None, is_gaming=lambda: gaming["on"])
    assert "obs" in w.pause_for and "discord" in w.pause_for
    w.pause_for = set()
    assert w._paused_reason() is None
    gaming["on"] = True; assert w._paused_reason() == "gaming"
    w.enabled.clear(); assert w._paused_reason() == "off"
    w.enabled.set(); gaming["on"] = False
    import psutil
    w.pause_for = {psutil.Process().name().lower()}
    assert w._paused_reason() == "camera app open"

def test_no_camera_means_none_not_a_crash(tmp_path):
    w = core.FaceWatcher({"index": 63}, str(tmp_path), lambda: None)
    assert w._open() is None

def test_start_wires_greeting_and_gaming(tmp_path):
    made = {}
    class W:
        def __init__(self, cfg, data_dir, on_arrive, is_gaming): made.update(cfg=cfg, dir=data_dir, arrive=on_arrive, gaming=is_gaming)
        def start(self): made["started"] = True
    class B: sir = "sir"; state_dir = str(tmp_path); weather_line = staticmethod(lambda: None)
    said = []
    assert glue.start({"camera": {"greet_with_time": False}}, B(), said.append, is_gaming=lambda: True, watcher_cls=W)
    made["arrive"]()
    assert said[0].startswith("Welcome back, sir. Good") and made["gaming"]() is True and made["started"]
    assert glue.start({"camera": {"disabled": True}}, B(), said.append, watcher_cls=W) is None

def test_voice_camera_commands(brain_factory, rec):
    b = brain_factory(rec)
    assert "Plug in a webcam" in b.handle("learn my face") or "switched off" in b.handle("learn my face")
    class Book:
        def forget(self): self.gone = True
    class Cam:
        enabled = threading.Event(); book = Book()
        def enroll(self): return True, "Got it, sir."
    b.camera = Cam()
    b.handle("learn my face")
    assert b.spoken[-2].startswith("Look straight at the camera") and b.spoken[-1] == "Got it, sir."
    assert "deleted" in b.handle("forget my face") and b.camera.book.gone
    b.handle("turn on the camera"); assert b.camera.enabled.is_set()
    b.handle("turn off the face greeting"); assert not b.camera.enabled.is_set()


def test_no_video_device_means_opencv_is_never_asked(tmp_path, monkeypatch):
    """Without /dev/video*, the re-check doesn't touch OpenCV (its V4L2 backend logs warnings every time)."""
    from jarvisd import camera
    from jarvisd.core import camera as core_cam
    opened = []
    monkeypatch.setattr(core_cam.FaceWatcher, "_open", lambda self: opened.append(1) or "cap")
    W = camera.quiet_watcher_cls(dev_root=str(tmp_path))
    w = W.__new__(W)
    assert w._open() is None and opened == []
    (tmp_path / "video0").write_text("")
    assert w._open() == "cap" and opened == [1]
