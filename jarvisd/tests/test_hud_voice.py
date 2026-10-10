import json, os
import numpy as np
from jarvisd import hud, voice_out, daemon

def test_hudbus_roundtrip(tmp_path):
    p = str(tmp_path / "hud.json"); bus = hud.HudBus(p, clock=lambda: 100.0)
    bus.thinking("open discord")
    st = hud.read_state(p)
    assert st["state"] == "thinking" and st["heard"] == "open discord" and st["heard_ts"] == 100.0

class FakeHud:
    def __init__(self): self.vis = False; self.log = []
    def isVisible(self): return self.vis
    def show(self): self.vis = True; self.log.append("show")
    def hide(self): self.vis = False; self.log.append("hide")
    def set_state(self, s): self.log.append(("state", s))
    def set_caption(self, t, secs=6): self.log.append(("caption", t))
    def set_heard(self, t, status="heard"): self.log.append(("heard", t))
    def set_level(self, l): pass

def test_follower_pops_up_and_hides():
    t = [0.0]; h = FakeHud(); f = hud.Follower(h, {"idle_hide_seconds": 4}, clock=lambda: t[0])
    assert f.tick({"state": "idle"}) is False and not h.vis
    f.tick({"state": "listening"}); assert h.vis
    f.tick({"state": "speaking", "caption": "Yes, sir.", "caption_ts": 1}); assert ("caption", "Yes, sir.") in h.log
    t[0] = 2.0; f.tick({"state": "idle", "caption": "Yes, sir.", "caption_ts": 1}); assert h.vis
    t[0] = 5.0; f.tick({"state": "idle", "caption": "Yes, sir.", "caption_ts": 1}); assert not h.vis
    assert h.log.count(("caption", "Yes, sir.")) == 1

def test_hud_renders_offscreen(tmp_path):
    img = hud.render_png(str(tmp_path / "h.png"), state="speaking", caption="Good evening, sir.")
    c = img.pixelColor(img.width() // 2, 320 // 2)
    assert os.path.getsize(tmp_path / "h.png") > 5000 and c.alpha() > 0

def test_voice_url_and_fetch(tmp_path):
    assert voice_out.voice_url("en_GB-alan-medium").endswith("/en/en_GB/alan/medium/en_GB-alan-medium.onnx")
    got = []
    def fetch(url, dest): got.append(url); open(dest, "w").write("x")
    p = voice_out.ensure_voice("en_GB-alan-medium", str(tmp_path), fetch)
    assert os.path.exists(p) and os.path.exists(p + ".json") and len(got) == 2
    voice_out.ensure_voice("en_GB-alan-medium", str(tmp_path), fetch); assert len(got) == 2

def test_speaker_falls_back_to_espeak():
    calls = []
    class R:
        returncode = 0
    def run(argv, **kw): calls.append(argv); return R()
    def bad_load(path): raise RuntimeError("no model offline")
    s = voice_out.Speaker({}, run=run, load_piper=bad_load)
    import time; time.sleep(0.2)
    s.speak("Good evening, sir.")
    assert calls[-1][0] == "espeak-ng" and calls[-1][-1] == "Good evening, sir."

def test_daemon_drives_hud():
    class Src:
        def __init__(self): self.n = 0
        def read(self):
            self.n += 1
            if self.n > 40: return None
            return (np.ones(1280, np.int16) * (3000 if 2 <= self.n <= 6 else 0))
    class Wake:
        def __init__(self): self.fired = False
        def score(self, c):
            if not self.fired: self.fired = True; return True, {}
            return False, None
        def reset(self): pass
    class Brain:
        sir = "sir"
        def __init__(self): self.got = []
        def handle(self, t): self.got.append(t)
        def say(self, t): pass
    ev = []
    class Hud:
        def listening(self): ev.append("listening")
        def thinking(self, heard=""): ev.append(("thinking", heard))
        def speaking(self, t): ev.append("speaking")
        def idle(self): ev.append("idle")
    b = Brain()
    daemon.Daemon(Src(), Wake(), lambda a: "open discord", b, hud=Hud(), max_commands=1).run()
    assert ev == ["listening", ("thinking", "open discord"), "idle"] and b.got == ["open discord"]
