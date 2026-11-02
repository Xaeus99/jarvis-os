"""rc9: tidier spoken web-search answers (no "One, ..." list number; the top source is named when the small model cites
none), Quick launch names that never elide (2-line names, measured per tile at paint time, re-measured on every width
change, the grid uses its whole width), and colour protocols ("purple protocol" with a short effect inside the
workspace window, "normal protocol" puts the old look back; "red theme" / "next theme" recolour quietly)."""
import io, json, os, time
import pytest
from jarvisd import websearch as WS, workspace as WSP, protocols as P, brain as B, hud as HUD
from jarvisd.core import commands, theme as T

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ================================================================ web search answers
def test_tidy_answer_drops_list_numbers_and_bullets():
    assert WS.tidy_answer("1. Mount Everest is 8,848.86 m (29,031.7 ft) tall.") == "Mount Everest is 8,848.86 m (29,031.7 ft) tall."
    assert WS.tidy_answer("1) It is tall [1].\n2) It is cold [2].") == "It is tall [1]. It is cold [2]."
    assert WS.tidy_answer("- A.\n* B.\n\u2022 C.") == "A. B. C."
    assert WS.tidy_answer("**Answer:** 42 is the answer.") == "42 is the answer."
    # numbers that belong to the answer stay
    assert WS.tidy_answer("2.5 million people live there.") == "2.5 million people live there."
    assert WS.tidy_answer("1990 was the year [1].") == "1990 was the year [1]."

FIX = """<html><body>
<div class="result results_links web-result "><h2 class="result__title">
<a rel="nofollow" class="result__a" href="https://example.org/peak">Example Peak facts</a></h2>
<a class="result__snippet" href="#">Example Peak is 8,849 metres tall.</a></div>
<div class="result results_links web-result "><h2 class="result__title">
<a rel="nofollow" class="result__a" href="https://b.example.net/p">B page</a></h2><a class="result__snippet" href="#">Second.</a></div>
</body></html>"""
PAGE = "<html><body><p>The summit of Example Peak stands 8,849 metres tall above sea level.</p></body></html>"

class _Resp(io.BytesIO):
    def __init__(self, b, ctype="text/html; charset=utf-8"):
        super().__init__(b.encode()); self.headers = {"Content-Type": ctype}
    def __enter__(self): return self
    def __exit__(self, *a): return False

def _opener(answer):
    def op(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req
        if "duckduckgo.com/html" in url:
            return _Resp(FIX)
        if url.endswith("/api/chat"):
            return _Resp(json.dumps({"message": {"content": answer}}), "application/json")
        return _Resp(PAGE)
    return op

class _AI:
    def __init__(self, opener):
        self.c = {"url": "http://127.0.0.1:11434"}; self.opener = opener; self.cfg = {}
    def ready(self): return True
    def model(self): return "llama3.2:3b"
    def available(self, in_game=False): return ["llama3.2:1b", "llama3.2:3b"]

def test_uncited_list_answer_is_spoken_plainly_with_the_top_source(tmp_path):
    op = _opener("1. Example Peak is 8,849 metres tall.")
    r = WS.WebSearch(_AI(op), opener=op, state=str(tmp_path / "s.json"), nvidia=False).answer("how tall is example peak")
    assert r["how"] == "brain"
    assert r["spoken"] == "Example Peak is 8,849 metres tall. That's from example.org."
    assert r["cited"] == [1] and r["text"].startswith("Example Peak is 8,849 metres tall.\n\nSources:")

def test_cited_answers_keep_their_own_sources(tmp_path):
    op = _opener("Example Peak is tall [2].")
    r = WS.WebSearch(_AI(op), opener=op, state=str(tmp_path / "s.json"), nvidia=False).answer("how tall is example peak")
    assert r["spoken"] == "Example Peak is tall. That's from b.example.net." and r["cited"] == [2]

def test_a_bare_list_number_falls_back_to_the_snippet(tmp_path):
    op = _opener("1.")
    r = WS.WebSearch(_AI(op), opener=op, state=str(tmp_path / "s.json"), nvidia=False).answer("how tall is example peak")
    assert r["how"] == "snippet" and r["spoken"].startswith("The top result, from example.org, says:")


# ================================================================ Quick launch labels
@pytest.fixture(scope="module")
def qt():
    from PySide6 import QtWidgets, QtCore
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return app, QtWidgets, QtCore

def _pump(app, n=20):
    from PySide6 import QtCore
    for _ in range(n):
        app.processEvents(); QtCore.QThread.msleep(5)

def _tiles(pl):
    return [pl.grid.itemAt(i).widget() for i in range(pl.grid.count())]

def _paint(app, pl):
    for t in _tiles(pl):
        t.grab()
    _pump(app, 2)

def test_split_label_and_grid_layout(qt):
    from PySide6 import QtGui
    from jarvisd.core import workspace as W
    fm = QtGui.QFontMetrics(W.tile_label_font(8.5))
    room = fm.horizontalAdvance("MINECRAFT") + 2
    assert W.split_label("BRAVE", fm, room) == ["BRAVE"]
    assert W.split_label("MINECRAFT LAUNCHER", fm, room) == ["MINECRAFT", "LAUNCHER"]
    assert W.split_label("SUPERCALIFRAGILISTIC", fm, room) is None
    assert W.tile_label_layout(["BRAVE", "STEAM"], 200) == (8.5, False)
    pt, two = W.tile_label_layout(["BRAVE", "MINECRAFT LAUNCHER"], room)
    assert two and pt == 8.5

def test_no_tile_name_is_elided_at_1280(qt, tmp_path, monkeypatch):
    app, QtWidgets, QtCore = qt
    monkeypatch.setattr(WSP, "CFG_PATH", str(tmp_path / "config.json")); monkeypatch.setattr(WSP, "STATE", str(tmp_path / "st"))
    w = WSP._real_window({}, QtWidgets, QtCore)
    w.resize(1280, 800); w.show(); _pump(app)
    pl = w.p_launch; _paint(app, pl)
    names = [(e.get("name") or "").upper() for e in pl.entries]
    got = [" ".join(t.label_lines) for t in _tiles(pl)]
    assert got == names and not any("\u2026" in g for g in got)
    # the grid uses the panel's whole width (no leftover columns squeezing the tiles)
    t0 = _tiles(pl)[0]
    assert t0.width() >= (pl._inner_w() - pl.GAP * (pl.cols() - 1)) / pl.cols() - 2
    w.hide(); w.deleteLater()

def test_label_size_follows_the_width(qt):
    app, QtWidgets, QtCore = qt
    from jarvisd.core import workspace as W
    p = W.LaunchPanel([{"name": n, "target": "x"} for n in ("Spotify", "Discord", "Fly Vault", "Settings")], lambda e: None)
    p.resize(900, 300); p.show(); _pump(app)
    wide = (p.label_pt, p.label_two)
    p.resize(230, 300); _pump(app); _paint(app, p)                 # same 4 columns... narrower tiles
    assert (p.label_pt, p.label_two) != wide or p.cols() != 4
    assert all("\u2026" not in " ".join(t.label_lines) for t in _tiles(p))
    p.deleteLater()

def test_two_line_names_get_a_taller_tile(qt):
    app, QtWidgets, QtCore = qt
    from jarvisd.core import workspace as W
    p = W.LaunchPanel([{"name": n, "target": "x"} for n in ("Brave", "Minecraft Launcher", "Counter Strike 2", "Files")], lambda e: None)
    p.resize(300, 300); p.show(); _pump(app); _paint(app, p)
    if p.label_two:
        lines = {t.entry.get("name"): t.label_lines for t in _tiles(p)}
        assert lines["Minecraft Launcher"] == ["MINECRAFT", "LAUNCHER"] and lines["Brave"] == ["BRAVE"]
        assert all(t.height() == p.tile_h() for t in _tiles(p)) and p.minimumHeight() == p.needed_height()
    p.deleteLater()


# ================================================================ protocols
@pytest.mark.parametrize("said,want", [
    ("purple protocol", ("engage", "purple", "vibranium")),
    ("Jarvis, engage purple protocol", ("engage", "purple", "vibranium")),
    ("initiate the blood red protocol", ("engage", "blood red", "ultron")),
    ("stealth protocol", ("engage", "stealth", "stealth")),
    ("protocol orange", ("engage", "orange", "hulkbuster")),
    ("normal protocol", ("normal", None, None)),
    ("end protocol", ("normal", None, None)),
    ("disengage the purple protocol", ("normal", None, None)),
    ("activate party protocol", ("unknown", "party", None)),
    ("what is the http protocol", None),
    ("explain the protocol", None),
    ("open steam", None),
])
def test_protocol_phrases(said, want):
    assert P.parse_voice(said) == want

@pytest.mark.parametrize("said,want", [
    ("red theme", ("theme", {"name": "mark3"})), ("next theme", ("theme", {"name": "next"})),
    ("switch the theme to orange", ("theme", {"name": "hulkbuster"})),
    ("purple protocol", ("protocol", {"kind": "engage", "word": "purple", "theme": "vibranium"})),
    ("dark mode", None), ("light mode", None),                     # Windows/KDE dark/light mode stays its own command
    ("search the web for tcp protocol", ("web_search", {"query": "tcp protocol"})),
])
def test_brain_routes(said, want):
    assert B.parse_os(commands.normalize(said)) == want

class _Hud:
    def __init__(self): self.pubs = []
    def publish(self, **kw): self.pubs.append(kw)

def _brain(tmp_path, gaming=False):
    said, sent = [], []
    b = B.LinuxBrain({}, state_dir=str(tmp_path / "s"), run=lambda argv, **k: (0, ""), say=lambda t: said.append(t) or t)
    b.cfg_path = str(tmp_path / "config.json"); b.hud = _Hud()
    b.ui_send = lambda name, msg: sent.append((name, msg)) or {"ok": True, "visible": True}
    b.is_gaming = lambda: gaming
    return b, said, sent

def test_purple_then_normal_protocol(tmp_path):
    b, said, sent = _brain(tmp_path)
    json.dump({"theme": "cyan", "vr": {"port": 1}}, open(b.cfg_path, "w"))
    b.handle("purple protocol")
    assert said[-1] == "Purple protocol engaged, sir."
    assert sent[-1] == ("workspace", {"cmd": "protocol", "title": "PURPLE PROTOCOL", "theme": "vibranium", "effect": True})
    c = json.load(open(b.cfg_path))
    assert c["theme"] == "vibranium" and c["protocol"]["base"] == "cyan" and c["vr"] == {"port": 1}
    assert b.hud.pubs[-1]["theme"] == "vibranium" and b.hud.pubs[-1]["theme_ts"] > 0
    b.handle("red protocol")                                      # a second protocol keeps the original look as the base
    assert json.load(open(b.cfg_path))["protocol"]["base"] == "cyan"
    b.handle("normal protocol")
    assert said[-1].startswith("Normal protocol, sir. Back to Arc Cyan")
    c = json.load(open(b.cfg_path)); assert c["theme"] == "cyan" and "protocol" not in c
    assert sent[-1][1]["title"] == "NORMAL PROTOCOL" and sent[-1][1]["theme"] == "cyan"
    b.handle("normal protocol")
    assert said[-1] == "No protocol is running, sir. This is the normal look."

def test_no_effect_in_a_game_and_unknown_protocols(tmp_path):
    b, said, sent = _brain(tmp_path, gaming=True)
    b.handle("stealth protocol")
    assert sent[-1][1]["effect"] is False and json.load(open(b.cfg_path))["theme"] == "stealth"
    b.handle("party protocol")
    assert "don't have a party protocol" in said[-1]

def test_theme_by_voice_is_quiet_and_becomes_normal(tmp_path):
    b, said, sent = _brain(tmp_path)
    b.handle("purple protocol"); b.handle("red theme")
    c = json.load(open(b.cfg_path)); assert c["theme"] == "mark3" and "protocol" not in c
    assert sent[-1][1]["effect"] is False and said[-1] == "Mark III (red and gold) theme, sir."
    b.handle("next theme")
    assert json.load(open(b.cfg_path))["theme"] == T.next_name("mark3")

def test_works_without_a_workspace_running(tmp_path):
    b, said, sent = _brain(tmp_path)
    b.ui_send = lambda name, msg: None                            # nothing listening
    b.handle("orange protocol")
    assert said[-1] == "Orange protocol engaged, sir." and json.load(open(b.cfg_path))["theme"] == "hulkbuster"

def test_effect_phases_recolour_under_the_blackout():
    assert WSP.protocol_phases(0.1)["crackle"] > 0 and not WSP.protocol_phases(0.1)["recolour"]
    mid = WSP.protocol_phases(0.75)
    assert mid["recolour"] and mid["black"] > 0.8 and mid["title"] > 0.9 and mid["orb"] > 0
    assert WSP.protocol_phases(WSP.PROTOCOL_SECONDS)["done"] and WSP.protocol_phases(WSP.PROTOCOL_SECONDS)["black"] == 0
    assert all(WSP.protocol_phases(t / 10)["black"] <= 0.92 for t in range(0, 20))
    assert 0.4 <= WSP.PROTOCOL_SECONDS <= 2.5

def test_workspace_plays_the_effect_and_recolours(qt, tmp_path, monkeypatch):
    app, QtWidgets, QtCore = qt
    monkeypatch.setattr(WSP, "CFG_PATH", str(tmp_path / "config.json")); monkeypatch.setattr(WSP, "STATE", str(tmp_path / "st"))
    w = WSP._real_window({"theme": "cyan"}, QtWidgets, QtCore)
    w.resize(1280, 800); w.show(); _pump(app)
    ctl = WSP.Controller(w)
    r = ctl.handle({"cmd": "protocol", "title": "PURPLE PROTOCOL", "theme": "vibranium", "effect": True})
    assert r["how"] == "effect" and T.current() == "cyan"         # the colour changes under the blackout, not before
    fl = w._protocol_flash
    assert fl.testAttribute(QtCore.Qt.WA_TransparentForMouseEvents) and fl.geometry() == w.rect()
    t0 = time.time()
    while time.time() - t0 < WSP.PROTOCOL_SECONDS + 0.6:
        app.processEvents(); QtCore.QThread.msleep(10)
    assert T.current() == "vibranium" and w._protocol_flash is None
    # quiet (hidden window or no effect): recolour at once
    w.hide(); _pump(app)
    assert ctl.handle({"cmd": "protocol", "title": "RED PROTOCOL", "theme": "mark3", "effect": True})["how"] == "quiet"
    assert T.current() == "mark3"
    assert ctl.handle({"cmd": "protocol", "title": "X", "theme": "nonsense"})["how"] == "no theme"
    T.set_theme("vibranium"); w.deleteLater()

def test_effect_frame_paints(qt):
    from PySide6 import QtGui, QtCore
    img = QtGui.QImage(640, 400, QtGui.QImage.Format_ARGB32); img.fill(QtGui.QColor(20, 40, 60))
    th = T.THEMES["vibranium"]
    p = QtGui.QPainter(img); ph = WSP.paint_protocol(p, QtCore.QRectF(img.rect()), 0.8, "PURPLE PROTOCOL", th["main"], th["light"]); p.end()
    assert ph["title"] > 0
    c = img.pixelColor(320, 200)                                  # bright, purple-ish orb/title in the middle
    assert c.red() + c.blue() > 200

class _Ring:
    def __init__(self): self.n = 0; self.vis = False
    def set_state(self, s): pass
    def set_caption(self, *a): pass
    def set_heard(self, *a): pass
    def set_level(self, v): pass
    def isVisible(self): return self.vis
    def show(self): self.vis = True
    def hide(self): self.vis = False
    def update(self): self.n += 1

def test_hud_ring_takes_a_new_protocol_colour_only(qt):
    clock = [1000.0]
    ring = _Ring(); f = HUD.Follower(ring, {}, clock=lambda: clock[0])
    T.set_theme("cyan")
    f.tick({"state": "idle", "theme": "ultron", "theme_ts": 900.0})      # from before this HUD started: ignored
    assert T.current() == "cyan" and ring.n == 0
    f.tick({"state": "idle", "theme": "ultron", "theme_ts": 1001.0})
    assert T.current() == "ultron" and ring.n == 1
    f.tick({"state": "idle", "theme": "ultron", "theme_ts": 1001.0})     # same publish: nothing more
    assert ring.n == 1
    T.set_theme("vibranium")

def test_daemon_gives_the_brain_the_hud():
    src = open(os.path.join(os.path.dirname(B.__file__), "daemon.py")).read()
    assert "brain.hud = hud" in src
