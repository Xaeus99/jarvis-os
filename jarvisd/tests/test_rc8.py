"""rc8: the workspace at 1280x800 (header buttons never cover a panel title; the Quick launch grid grows with its tiles,
so JARVIS's four web apps are tiles too; the panels around it give way) and web search on a PC without an NVIDIA GPU
(the 1B model, the top pages trimmed to their relevant sentences, a tighter prompt and a 75 s limit; snippet fallback)."""
import io, json, os
import pytest
from jarvisd import websearch as WS, workspace as WSP

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ================================================================ workspace layout
@pytest.fixture(scope="module")
def qt():
    from PySide6 import QtWidgets, QtCore
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return app, QtWidgets, QtCore

def _pump(app, n=20):
    from PySide6 import QtCore
    for _ in range(n):
        app.processEvents(); QtCore.QThread.msleep(5)

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(WSP, "CFG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(WSP, "STATE", str(tmp_path / "state"))
    return tmp_path

def _win(qt, home, size, cfg=None):
    app, QtWidgets, QtCore = qt
    w = WSP._real_window(dict(cfg or {}), QtWidgets, QtCore)
    w.resize(*size); w.show(); _pump(app)
    return app, w

def test_webapp_tiles_join_quick_launch_once():
    base = [{"name": "Brave", "target": "brave"}]
    tiles, changed = WSP.with_webapp_tiles({}, base)
    assert changed and [t["target"] for t in tiles] == ["brave", "jarvis-vr-web.desktop", "jarvis-aim-web.desktop",
                                                         "jarvis-orbs-web.desktop", "jarvis-fly-web.desktop"]
    assert [t["name"] for t in tiles[1:]] == ["JARVIS VR", "Aim", "Orbs", "Fly Vault"]
    # his own list keeps its order; a tile already there isn't doubled
    own = {"workspace": {"quick_launch": [{"name": "Game", "target": "steam://run/1"}, {"name": "VR", "target": "jarvis-vr-web.desktop"}]}}
    tiles, changed = WSP.with_webapp_tiles(own, base)
    assert changed and [t["target"] for t in tiles][:2] == ["steam://run/1", "jarvis-vr-web.desktop"] and len(tiles) == 5
    # once done, a tile he removed stays removed
    done = {"workspace": {"webapp_tiles": 1, "quick_launch": [{"name": "Brave", "target": "brave"}]}}
    assert WSP.with_webapp_tiles(done, base) == ([{"name": "Brave", "target": "brave"}], False)
    assert all(WSP.launch_argv(t["target"])[0] == "gtk-launch" for t in WSP.webapp_tiles())

def test_first_open_saves_the_tiles_and_the_mark(qt, home):
    app, w = _win(qt, home, (1280, 756))
    saved = json.load(open(home / "config.json"))
    assert saved["workspace"]["webapp_tiles"] == 1 and len(saved["workspace"]["quick_launch"]) == 12
    assert len(w.p_launch.entries) == 12
    w.hide(); w.deleteLater()

@pytest.mark.parametrize("size", [(1080, 680), (1126, 680), (1280, 756), (1366, 728), (1480, 900), (1920, 1040)])
def test_right_column_never_overlaps_and_every_tile_shows(qt, home, size):
    app, w = _win(qt, home, size)
    col = [w.p_tab, w.p_media, w.p_launch, w.p_clips]
    for a, b in zip(col, col[1:]):
        assert a.geometry().bottom() < b.geometry().top(), (size, a.title, a.geometry(), b.title, b.geometry())
    pl = w.p_launch
    assert pl.height() >= pl.needed_height() and pl.minimumHeight() == pl.needed_height()
    tiles = [pl.grid.itemAt(i).widget() for i in range(pl.grid.count())]
    assert len(tiles) == 12
    for t in tiles:
        g = t.geometry().translated(pl.grid_host.pos())
        assert t.isVisible() and g.bottom() <= pl.height() and g.right() <= pl.width(), (size, t.entry, g)
        assert t.height() == pl.tile_h()
    w.hide(); w.deleteLater()

@pytest.mark.parametrize("size", [(1080, 680), (1280, 756), (1920, 1040)])
def test_header_buttons_never_cover_the_title(qt, home, size):
    app, w = _win(qt, home, size)
    for p in w.panels:
        shown = [b for b in p.header_widgets if b.isVisible()]
        for b in shown:
            assert b.x() >= p.title_right(), (size, p.title, b.text(), b.x(), p.title_right())
    pl = w.p_launch
    apps = w._webapps_button
    assert apps.isVisible() and pl.b_edit.isVisible()
    assert apps.text() in ("JARVIS APPS", "APPS") and apps.x() + apps.width() <= pl.b_edit.x()
    w.hide(); w.deleteLater()

def test_quick_launch_header_goes_short_then_gives_way(qt, home):
    app, w = _win(qt, home, (1280, 756))
    pl, apps = w.p_launch, w._webapps_button
    pl.resize(300, pl.height()); pl._place_header()
    assert apps.text() == "APPS" and apps.isVisible() and apps.x() >= pl.title_right()
    pl.resize(420, pl.height()); pl._place_header()
    assert apps.text() == "JARVIS APPS"
    pl.resize(240, pl.height()); pl._place_header()          # far too narrow: EDIT (rightmost) wins, APPS waits
    assert pl.b_edit.isVisible() and not apps.isVisible()
    pl.resize(420, pl.height()); pl._place_header()
    assert apps.isVisible()
    w.hide(); w.deleteLater()

def test_grid_grows_with_its_tiles(qt):
    app, QtWidgets, QtCore = qt
    from jarvisd.core import workspace as W
    p = W.LaunchPanel([{"name": f"T{i}", "target": "x"} for i in range(4)], lambda e: None)
    p.resize(340, 400); p.show(); _pump(app)
    h4 = p.minimumHeight(); r4 = p.rows()
    p.entries += [{"name": f"U{i}", "target": "y"} for i in range(8)]; p.rebuild(); _pump(app)
    assert p.rows() > r4 and p.minimumHeight() > h4 and p.minimumHeight() == p.needed_height()
    p.set_editing(True); _pump(app)                          # the ADD tile can open a new row
    assert p.needed_height() >= p.minimumHeight() - 1
    # one label size for the whole grid, smaller only when a name needs it
    assert W.tile_label_pt(["BRAVE"], 80) == 8.5 and W.tile_label_pt(["A VERY LONG TILE NAME"], 60) < 8.5
    p.deleteLater()

def test_small_screen_first_open_fills_it(qt, home):
    app, QtWidgets, QtCore = qt
    from PySide6 import QtGui
    av = QtGui.QGuiApplication.primaryScreen().availableGeometry()
    w = WSP._real_window({}, QtWidgets, QtCore)
    fits = av.width() >= w.minimumWidth() and av.height() >= w.minimumHeight()
    assert w._want_max == (fits and (av.width() < 1400 or av.height() < 860))
    w.deleteLater()


# ================================================================ web search without an NVIDIA GPU
FIX = """<html><body>
<div class="result results_links web-result "><h2 class="result__title">
<a rel="nofollow" class="result__a" href="https://example.org/peak">Example Peak facts</a></h2>
<a class="result__snippet" href="#">Example Peak is 8,849 metres tall.</a></div>
<div class="result results_links web-result "><h2 class="result__title">
<a rel="nofollow" class="result__a" href="https://b.example.net/p">B page</a></h2><a class="result__snippet" href="#">Second.</a></div>
<div class="result results_links web-result "><h2 class="result__title">
<a rel="nofollow" class="result__a" href="https://c.example.net/p">C page</a></h2><a class="result__snippet" href="#">Third.</a></div>
<div class="result results_links web-result "><h2 class="result__title">
<a rel="nofollow" class="result__a" href="https://d.example.net/p">D page</a></h2><a class="result__snippet" href="#">Fourth.</a></div>
<div class="result results_links web-result "><h2 class="result__title">
<a rel="nofollow" class="result__a" href="https://e.example.net/p">E page</a></h2><a class="result__snippet" href="#">Fifth.</a></div>
</body></html>"""
FILLER = " ".join(f"Paragraph {i} talks about valleys, rivers and the weather in spring." for i in range(40))
PAGE = f"<html><body><p>{FILLER} The summit of Example Peak stands 8,849 metres tall above sea level. {FILLER}</p></body></html>"

class _Resp(io.BytesIO):
    def __init__(self, b, ctype="text/html; charset=utf-8"):
        super().__init__(b.encode()); self.headers = {"Content-Type": ctype}
    def __enter__(self): return self
    def __exit__(self, *a): return False

def _opener(chat, timeouts, slow=False):
    def op(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req
        if "duckduckgo.com/html" in url:
            return _Resp(FIX)
        if url.endswith("/api/chat"):
            timeouts.append(timeout); chat.append(json.loads(req.data))
            if slow:
                raise TimeoutError("timed out")
            return _Resp(json.dumps({"message": {"content": "Example Peak is 8,849 metres tall [1]."}}), "application/json")
        return _Resp(PAGE)
    return op

class _AI:
    def __init__(self, opener, have=("llama3.2:1b", "llama3.2:3b", "qwen2.5:3b"), cfg=None):
        self.c = {"url": "http://127.0.0.1:11434"}; self.opener = opener; self.have = list(have); self.cfg = cfg or {}
    def ready(self): return True
    def model(self): return "llama3.2:3b"
    def available(self, in_game=False): return self.have

def test_relevant_text_keeps_the_answering_sentence():
    t = WS.page_text(PAGE, 8000)
    out = WS.relevant_text("how tall is example peak", t, 600)
    assert "8,849 metres" in out and len(out) <= 600 and len(t) > 4000
    assert WS.relevant_text("x", "short text.", 600) == "short text."

def test_cpu_search_uses_the_small_model_and_a_short_prompt(tmp_path):
    chat, tos = [], []
    op = _opener(chat, tos)
    ws = WS.WebSearch(_AI(op), opener=op, state=str(tmp_path / "s.json"), nvidia=False)
    r = ws.answer("how tall is example peak")
    assert r["how"] == "brain" and r["spoken"] == "Example Peak is 8,849 metres tall. That's from example.org."
    body = chat[0]
    assert body["model"] == "llama3.2:1b" and body["options"]["num_ctx"] == 2048 and body["options"]["num_predict"] <= 120
    assert tos == [75] and body["messages"][0]["content"] == WS.SYSTEM_CPU
    user = body["messages"][1]["content"]
    assert "8,849 metres" in user and user.count("\n[") == 3 and len(user) < 2400     # 3 trimmed sources
    assert ws.last_brain["model"] == "llama3.2:1b" and ws.last_brain["profile"] == "cpu"
    assert len(r["sources"]) == 5 and "[5] E page" in r["text"]                       # the notification keeps every link

def test_gpu_search_keeps_the_full_prompt(tmp_path):
    chat, tos = [], []
    op = _opener(chat, tos)
    r = WS.WebSearch(_AI(op), opener=op, state=str(tmp_path / "s.json"), nvidia=True).answer("how tall is example peak")
    assert r["how"] == "brain" and chat[0]["model"] == "llama3.2:3b" and chat[0]["options"]["num_ctx"] == 4096 and tos == [150]
    assert chat[0]["messages"][0]["content"] == WS.SYSTEM

def test_cpu_search_without_the_small_model_picks_the_smallest_there_is(tmp_path):
    chat, tos = [], []
    op = _opener(chat, tos)
    WS.WebSearch(_AI(op, have=("llama3.2:3b", "qwen2.5:3b")), opener=op, state=str(tmp_path / "s.json"), nvidia=False).answer("example peak")
    assert chat[0]["model"] == "qwen2.5:3b"
    chat.clear()
    cfg = {"assistant": {"search_model": "llama3.2:3b", "search_profile": "gpu"}}
    ws = WS.WebSearch(_AI(op, cfg=cfg), opener=op, state=str(tmp_path / "s.json"), nvidia=False)
    assert ws.profile()[0] == "gpu"; ws.answer("example peak")
    assert chat[0]["model"] == "llama3.2:3b" and chat[0]["options"]["num_ctx"] == 4096

def test_cpu_timeout_still_falls_back_to_the_snippet(tmp_path):
    chat, tos = [], []
    op = _opener(chat, tos, slow=True)
    r = WS.WebSearch(_AI(op), opener=op, state=str(tmp_path / "s.json"), nvidia=False).answer("how tall is example peak")
    assert r["how"] == "snippet" and r["spoken"].startswith("The top result, from example.org, says:") and "8,849" in r["spoken"]
    assert tos == [75]

def test_nvidia_detection_is_cached():
    WS._NVIDIA.clear(); n = []
    assert WS.has_nvidia(lambda: n.append(1) or False) is False
    assert WS.has_nvidia(lambda: n.append(1) or True) is False and n == [1]
    WS._NVIDIA.clear()
