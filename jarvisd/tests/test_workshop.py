"""Hologram workshop, vitals HUD, update countdown and 3D brain view (Windows JARVIS v5.6 parity): parsing, safety,
recipes, the window protocol, the countdown, the brain feed and real offscreen renders. No model, no GPU, no network."""
import datetime, io, json, os, re
import pytest
from jarvisd import workshop as W, brainfeed as BF
from jarvisd.core import holo, brainbus as bb

class FakeBrain:
    sir = "sir"; cfg = {}
    def __init__(self): self.spoken = []; self.gaming = False
    def say(self, t): self.spoken.append(t); return t
    def is_gaming(self): return self.gaming

@pytest.fixture
def ws(tmp_path):
    launched = []
    b = FakeBrain()
    w = W.Workshop(b, state=str(tmp_path), launch=lambda kind: launched.append(kind) or True,
                   close=lambda kind, state=None: launched.append("close:" + kind), threaded=False)
    w.launched = launched
    return w

def fake_window(tmp_path, kind):
    (tmp_path / f"win_{kind}.pid").write_text(str(os.getpid()))   # "open": a live pid

def test_parse_same_phrases_as_windows():
    P = W.parse
    assert P("Jarvis, show me a 3D model of Earth") == ("holo_show", {"what": "earth", "raw": "Earth"})
    assert P("visualize a rocket")[1]["what"] == "rocket"
    assert P("hologram of a futuristic motorcycle")[1]["what"] == "futuristic motorcycle"
    assert P("design a new suit in purple and gold") == ("holo_show", {"what": "suit in purple and gold"})
    assert P("open the hologram workshop") == ("holo_show", {"what": ""})
    assert P("save this model") == ("holo_save", {}) and P("close the hologram") == ("holo_close", {})
    assert P("show vitals") == ("vitals", {}) and P("how's my pc doing") == ("vitals", {})
    assert P("when's the next update") == ("eta_show", {}) and P("hide the countdown") == ("eta_hide", {})
    assert P("show your brain") == ("brain", {}) and P("thought cloud") == ("brain", {})
    for other in ("make an image of a cat", "what time is it", "open steam", "write a python script that adds numbers"):
        assert P(other) is None, other

@pytest.mark.parametrize("text,raw,kind", [
    ("a naked woman", None, "adult"), ("a nude figure", None, "adult"), ("a gun", None, "gore"), ("a bloody corpse", None, "gore"),
    ("taylor swift", "Taylor Swift", "real_person"), ("jake miller", "Jake Miller", "real_person"),
    ("my friend", None, "real_person"), ("me", None, "real_person"), ("a swastika", None, "hate")])
def test_blocklists_apply_to_holograms(text, raw, kind):
    assert W.check_text(text, raw) == (False, kind)

@pytest.mark.parametrize("text", ["earth", "a rocket", "the Death Star", "a human skeleton", "a dragon", "Iron Man suit", "DNA"])
def test_ordinary_requests_pass(text):
    assert W.check_text(text, text)[0], text

def test_blocklist_fails_closed(monkeypatch):
    import jarvisd.safety as S
    monkeypatch.delattr(S, "check_prompt")
    assert holo.check_text("earth") == (False, "checks_unavailable")

def test_every_preset_builds_and_is_never_photoreal():
    names = set(holo.PRESETS)
    assert {"earth", "suit", "mannequin", "solar", "dna", "atom", "car", "rocket"} <= names
    for n in names:
        sc = W.scene_from_recipe({"preset": n, "params": {}})
        assert sc and sc["parts"] or sc["lines"], n
        assert all(p["shape"] in holo.SHAPES for p in sc["parts"])     # glowing primitives only: no textures, no photos
    m = holo.build("mannequin", skeleton=True, organs=True, muscles=True)
    assert "mannequin" in m["title"].lower()
    assert not any(re.search(r"logo|marvel|stark", str(p.get("label") or ""), re.I) for p in holo.build("suit")["parts"])

def test_recipes_and_suit_words():
    r = W.recipe_for("suit in purple and gold with wings")
    assert r == {"preset": "suit", "params": {"primary": "purple", "secondary": "gold", "shoulders": 1.0, "wings": True, "stealth": False}}
    assert W.recipe_for("earth and the moon") == {"preset": "earth", "params": {"moon": True}}
    assert W.recipe_for("human skeleton")["params"]["skeleton"] is True
    assert W.recipe_for("futuristic motorcycle") is None            # free-form
    assert W.scene_from_recipe({"preset": "rm -rf /"}) is None
    assert W.scene_from_recipe({"preset": "suit", "params": {"__class__": 1, "primary": "red"}})["title"]

def test_freeform_spec_is_validated_and_checked():
    good = json.dumps({"title": "hover bike", "parts": [{"shape": "torus", "pos": [1, 0, 0], "scale": [1, 1, 1]},
                                                          {"shape": "box", "pos": [0, 0.5, 0], "scale": [2, 0.3, 0.4], "label": "seat"}]})
    sc = W.scene_from_recipe({"spec": good})
    assert sc["title"] == "hover bike" and len(sc["parts"]) == 2 and sc["freeform"]
    assert W.scene_from_recipe({"spec": good.replace("hover bike", "naked lady")}) is None
    assert W.scene_from_recipe({"spec": good.replace('"seat"', '"gore"')}) is None
    many = json.dumps({"title": "x", "parts": [{"shape": "sphere", "pos": [i % 5, 0, 0], "scale": [1, 1, 1]} for i in range(900)]})
    assert len(W.scene_from_recipe({"spec": many})["parts"]) <= holo.MAX_PRIMS
    assert W.scene_from_recipe({"spec": "__import__('os').system('x')"}) is None

def test_show_writes_recipe_and_opens_window(ws, tmp_path):
    out = ws.handle(*W.parse("show me a 3D model of Earth"))
    assert "Earth" in out and ws.launched == ["holo"]
    d = json.loads((tmp_path / "holo.json").read_text())
    assert d["recipe"]["preset"] == "earth" and d["seq"] == 1
    fake_window(tmp_path, "holo")
    ws.handle(*W.parse("visualize a rocket"))
    assert ws.launched == ["holo"]                                   # already open: just a new recipe
    assert json.loads((tmp_path / "holo.json").read_text())["recipe"]["preset"] == "rocket"

def test_refusals_and_game_mode(ws, tmp_path):
    assert "real people" in ws.handle(*W.parse("show me a 3D model of Taylor Swift"))
    assert "not something I'll build" in ws.handle("holo_show", {"what": "a bloody corpse"})
    ws.b.gaming = True
    assert "games" in ws.handle(*W.parse("show me a 3D model of Earth"))
    assert "game" in ws.handle("vitals", {}) and "game" in ws.handle("brain", {})
    assert ws.launched == [] and not (tmp_path / "holo.json").exists()

def test_voice_edits_only_while_open(ws, tmp_path):
    ws.handle(*W.parse("design a new suit"))
    assert ws.edit("make it purple and gold") is None                # window not open: not an edit
    fake_window(tmp_path, "holo")
    assert ws.edit("make it purple and gold") == "Repainted, sir."
    assert ws.edit("add wings") == "Wings deployed, sir."
    assert ws.edit("bigger shoulders") == "Shoulders up-armoured, sir."
    d = json.loads((tmp_path / "holo.json").read_text())
    assert d["recipe"]["params"] == {"primary": "purple", "secondary": "gold", "shoulders": 1.25, "wings": True, "stealth": False}
    assert d["keep_camera"] is True
    assert ws.edit("make it plaid") is None                          # unknown colour: not an edit
    assert ws.edit("spin faster") == "Faster, sir."
    assert json.loads((tmp_path / "holo.json").read_text())["view"]["what"] == "faster"
    assert ws.edit("what time is it") is None

def test_freeform_falls_back_without_model(ws, tmp_path):
    ws.coder = lambda what, cfg=None: None
    out = ws.handle(*W.parse("hologram of a race car with spoilers"))
    assert "need the coding model" in out
    assert json.loads((tmp_path / "holo.json").read_text())["recipe"]["preset"] == "car"
    ws.coder = lambda what, cfg=None: '{"title": "hover board", "parts": [{"shape": "box", "pos": [0,0,0], "scale": [3, 0.2, 1]}]}'
    assert "Designed: hover board" in ws.handle(*W.parse("visualize a hoverboard"))
    ws.coder = lambda what, cfg=None: '{"title": "corpse", "parts": [{"shape": "box", "pos": [0,0,0], "scale": [1,1,1]}]}'
    assert "couldn't design" in ws.handle(*W.parse("visualize a thingamajig"))

def test_save_asks_the_window(ws, tmp_path, monkeypatch):
    assert "no hologram" in ws.handle("holo_save", {})
    ws.handle(*W.parse("show me a 3d model of dna"))
    monkeypatch.setattr("jarvisd.creator.user_dir", lambda which: str(tmp_path / "Docs"))
    ws.handle("holo_save", {})
    assert json.loads((tmp_path / "holo.json").read_text())["save"]["folder"].endswith("JARVIS Models")

def test_eta_clean_set_and_spoken(tmp_path):
    assert W.clean_eta({"version": "0.2; rm", "percent": "40", "eta_iso": "nope"}) is None
    eta = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)).isoformat()
    d = W.set_eta({"version": "0.2.0", "percent": 140, "eta_iso": eta, "step": "testing <b>"}, state=str(tmp_path))
    assert d["percent"] == 100 and d["step"] == "testing b"
    line = W.eta_line(W.get_eta(str(tmp_path)))
    assert "Version 0.2.0 is 100 percent built" in line and "minutes to go" in line
    assert "No update" in W.eta_line(None)

def test_tablink_update_eta_action(tmp_path, monkeypatch):
    from jarvisd import tablink
    monkeypatch.setattr(W, "STATE", str(tmp_path))
    r = tablink.ActionRunner(hands=None, say=lambda t: None)
    eta = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)).isoformat()
    out = r.run({"mid": "m1", "actions": [{"type": "update_eta", "version": "0.2", "percent": 55, "eta_iso": eta}]})
    assert out["results"][0]["ok"] and json.loads((tmp_path / "update_eta.json").read_text())["percent"] == 55
    bad = r.run({"mid": "m2", "actions": [{"type": "update_eta", "version": "", "percent": 5, "eta_iso": eta}]})
    assert not bad["results"][0]["ok"]

def test_brain_feed_writes_only_while_open_and_redacts(tmp_path):
    f = BF.Feed(state=str(tmp_path), is_open=lambda: False)
    assert f.begin("race_a", "llama3.2:1b") is False and not (tmp_path / BF.EVENTS).exists()
    f.is_open = lambda: True
    f.begin("race_a", "llama3.2:1b", "racing"); f.tokens("race_a", 3); f.flash("race_a"); f.end("race_a")
    f.note("memory", "in", "my password is hunter2 and my email is a@b.co")
    lines = (tmp_path / BF.EVENTS).read_text().splitlines()
    assert "hunter2" not in "".join(lines) and "a@b.co" not in "".join(lines)
    bus = bb.BrainBus()
    assert BF.replay(bus, lines + ["garbage", json.dumps({"k": "begin", "r": "nope"})]) == 5
    info = bus.region_info("race_a")
    assert info["model"] == "llama3.2:1b" and info["tokens"] == 3 and info["fired"] == 1 and not info["busy"]

def test_brain_tail_reads_only_new_lines(tmp_path):
    p = tmp_path / BF.EVENTS
    p.write_text('{"k":"begin","r":"coder"}\n')
    t = BF.Tail(str(p))
    assert t.read() == []
    with open(p, "a") as fh:
        fh.write('{"k":"end","r":"coder"}\n')
    assert t.read() == ['{"k":"end","r":"coder"}']

def test_brain_hooks_on_race(monkeypatch, tmp_path):
    from jarvisd import race
    f = BF.Feed(state=str(tmp_path), is_open=lambda: True)
    monkeypatch.setattr(race, "stream_ollama", lambda base, model, msgs, **k: iter(["Hello", " sir."]))
    monkeypatch.setattr(race, "race", race.race)
    done = BF.install(f)
    assert "race.stream_ollama" in done
    assert list(race.stream_ollama("http://x", "qwen2.5:3b", [])) == ["Hello", " sir."]
    kinds = [json.loads(l)["k"] for l in (tmp_path / BF.EVENTS).read_text().splitlines()]
    assert kinds[0] == "begin" and kinds.count("tok") == 2 and kinds[-1] == "end"

def test_windows_render_offscreen(tmp_path):
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from jarvisd import holowin
    from jarvisd.core.holoview import render_png, HoloWindow
    from jarvisd.core import hud56
    from jarvisd.core.brainview import BrainWindow
    for n in ("earth", "suit", "mannequin"):
        p = render_png(W.scene_from_recipe({"preset": n, "params": {}}), str(tmp_path / f"{n}.png"), 320, 200)
        assert os.path.getsize(p) > 2000
    win = HoloWindow(None)
    (tmp_path / "holo.json").write_text(json.dumps({"seq": 3, "recipe": {"preset": "atom", "params": {}}, "view": {"seq": 3, "what": "stop"}}))
    f = holowin.HoloFollower(win, str(tmp_path)); assert f.tick()
    assert win.canvas.scene["title"].startswith("Atom") and win.canvas.auto_spin == 0.0
    assert not f.tick()                                               # nothing new
    v = hud56.VitalsWindow(hud56.VitalsSampler(gpu=lambda: {}), start_thread=False)
    c = hud56.CountdownWindow(lambda: None)
    bw = BrainWindow(bb.BrainBus())
    for w in (win, v, c, bw):
        w.resize(400, 300); assert not w.grab().isNull(); w.close()

def test_save_model_files(tmp_path):
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from jarvisd import holowin
    p = holowin.save_model(holo.build("rocket"), str(tmp_path))
    stem = p[:-5]
    assert all(os.path.getsize(stem + ext) > 0 for ext in (".json", ".png", ".obj"))

def test_brain_routes_workshop_before_makers(tmp_path, monkeypatch):
    from jarvisd import brain as B, workshop
    monkeypatch.setattr(workshop, "open_window", lambda kind, run=None: True)
    b = B.LinuxBrain(state_dir=str(tmp_path), run=lambda *a, **k: (0, ""))
    b.workshop = W.Workshop(b, state=str(tmp_path), launch=lambda kind: True, threaded=False)
    assert "Earth" in b.handle("Jarvis, show me a 3D model of Earth")
    assert "thought cloud" in b.handle("show your brain")


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

def _opener(chunks, have=True, seen=None):
    def op(rq, timeout=None):
        url = rq.full_url
        if url.endswith("/api/tags"):
            return _Resp(json.dumps({"models": [{"name": "qwen2.5-coder:3b"}] if have else []}).encode())
        body = json.loads(rq.data)
        if seen is not None: seen.append(body)
        r = _Resp(b"".join(json.dumps({"message": {"content": c}, "done": i == len(chunks) - 1}).encode() + b"\n" for i, c in enumerate(chunks)))
        if seen is not None: seen.append(r)
        return r
    return op

def test_ask_coder_streams_and_stops_at_the_deadline():
    seen = []
    txt = W.ask_coder("a hover bike", opener=_opener(['{"title": "hover', ' bike", "parts": []}'], seen=seen))
    assert txt == '{"title": "hover bike", "parts": []}'
    assert seen[0]["stream"] is True and seen[0]["format"] == "json" and seen[1].closed
    assert W.ask_coder("x", opener=_opener(["a"], have=False)) is None            # no coding model: None
    ticks = iter([0, 1, 999, 999, 999])
    seen = []
    assert W.ask_coder("x", opener=_opener(["{", "a", "b", "}"], seen=seen), deadline=10, clock=lambda: next(ticks)) is None
    assert seen[1].closed                                                        # connection closed -> Ollama stops
