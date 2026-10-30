"""rc7: spoken "are you sure?" answers on the desktop (daemon Asker), the VR CGNAT option by voice, Fly Vault as JARVIS's
fourth web app, and "search the web for ..." answered out loud with its sources (DuckDuckGo + the offline brain)."""
import configparser, io, json, pathlib, threading, time, types
import numpy as np
import pytest
from jarvisd import daemon as D, vrpair as P, webapps, websearch as WS, workspace, brain as B
from jarvisd.core import commands

ROOT = pathlib.Path(__file__).resolve().parents[2]
SF = ROOT / "image" / "system_files"


# ================================================================ confirmations (Asker)
class _Src:
    """A microphone: `script` is a list of chunks (int16 arrays); then silence, or None to end."""
    def __init__(self, script=(), end=False):
        self.script = list(script); self.end = end
        import queue
        self.q = queue.Queue()
    def read(self):
        if self.script:
            return self.script.pop(0)
        if self.end:
            return None
        return np.zeros(D.CHUNK, np.int16)

def _loud_chunks(n=6):
    return [(np.ones(D.CHUNK) * 3000).astype(np.int16) for _ in range(n)]

def test_loud_gate():
    assert not D._loud(np.zeros(D.CHUNK * 10, np.int16))
    assert D._loud(np.concatenate([np.zeros(D.CHUNK * 5, np.int16)] + _loud_chunks(1)))
    assert not D._loud(np.zeros(0, np.int16))

def test_typed_answer_is_taken_and_not_run_as_a_command():
    said = []
    a = D.Asker(said.append, {}, timeout=3)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("ans", a.ask("Restart into Windows now, sir?")))
    t.start()
    for _ in range(100):
        if a.waiting(): break
        time.sleep(0.01)
    assert a.waiting()
    assert a.offer("yes", by="typed") is True
    t.join(3)
    assert out["ans"] == "yes" and said == ["Restart into Windows now, sir?"] and a.last[2] == "typed"
    assert not a.waiting() and a.offer("yes") is False       # nothing asked any more: a normal command again

def test_no_answer_is_no():
    a = D.Asker(lambda q: None, {}, timeout=0.2)
    assert a.ask("Go ahead, sir?") == ""

def test_own_question_heard_back_is_ignored():
    a = D.Asker(lambda q: None, {}, timeout=1)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("ans", a.ask("Shall I go ahead and turn it on, sir?")))
    t.start()
    while not a.waiting(): time.sleep(0.01)
    assert a.offer("shall I go ahead and turn it on sir", by="voice") is False   # the speakers, not him
    assert a.offer("yes please", by="voice") is True
    t.join(2)
    assert out["ans"] == "yes please"

def test_voice_answer_when_asked_from_the_voice_loop():
    """The voice loop asks (a spoken command needed a yes): JARVIS listens straight away, no wake word."""
    heard = []
    class Wake:
        n = 0
        def score(self, c):
            self.n += 1
            return (self.n == 1), {}
        def reset(self): pass
    src = _Src(_loud_chunks(4) + [np.zeros(D.CHUNK, np.int16)] * 14 + _loud_chunks(4) + [np.zeros(D.CHUNK, np.int16)] * 14, end=True)
    texts = iter(["switch to windows", "yes"])
    class Brain:
        sir = "sir"
        def say(self, t): pass
        def handle(self, text):
            heard.append(("cmd", text))
            heard.append(("answer", self.ask("Restart into Windows now, sir?")))
    br = Brain()
    box = {}
    a = D.Asker(lambda q: heard.append(("asked", q)), box, timeout=5)
    br.ask = a.ask
    d = D.Daemon(src, Wake(), lambda audio: next(texts), br, asker=a)
    box["daemon"] = d
    d.run()
    assert heard == [("cmd", "switch to windows"), ("asked", "Restart into Windows now, sir?"), ("answer", "yes")]
    assert a.last == ("Restart into Windows now, sir?", "yes", "voice")

def test_voice_loop_hands_heard_words_to_a_waiting_question():
    """A typed command asked: the voice loop listens (force) and its words go to the question, not the brain."""
    class Wake:
        def score(self, c): return False, {}
        def reset(self): pass
    handled = []
    br = types.SimpleNamespace(sir="sir", say=lambda t: None, handle=lambda t: handled.append(t))
    box = {}
    a = D.Asker(lambda q: None, box, timeout=3)
    src = _Src([np.zeros(D.CHUNK, np.int16)] * 3 + _loud_chunks(5), end=False)
    d = D.Daemon(src, Wake(), lambda audio: "yeah go ahead", br, asker=a)
    box["daemon"] = d
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("ans", a.ask("Turn it on, sir?")))
    th.start()
    dt = threading.Thread(target=d.run, daemon=True); dt.start()
    th.join(4)
    src.end = True
    assert out["ans"] == "yeah go ahead" and handled == []

def test_silence_while_waiting_is_not_an_answer_and_not_a_complaint():
    class Wake:
        def score(self, c): return False, {}
        def reset(self): pass
    said = []
    br = types.SimpleNamespace(sir="sir", say=said.append, handle=lambda t: None)
    box = {}
    a = D.Asker(lambda q: None, box, timeout=0.8)
    src = _Src([], end=False)
    stt_calls = []
    d = D.Daemon(src, Wake(), lambda audio: stt_calls.append(1) or "Thank you.", br, asker=a)
    box["daemon"] = d
    dt = threading.Thread(target=d.run, daemon=True); dt.start()
    ans = a.ask("Turn it on, sir?")
    src.end = True
    assert ans == "" and stt_calls == [] and not any("didn't catch" in s for s in said)

def test_daemon_main_wires_the_asker():
    src = (ROOT / "jarvisd" / "jarvisd" / "daemon.py").read_text()
    assert "brain.ask = asker.ask" in src and "asker.offer(msg[\"text\"].strip(), by=\"typed\")" in src
    assert "Daemon(mic, wake, stt, brain, hud=hud, asker=asker)" in src

def test_brain_confirmation_really_runs_the_action_after_yes(brain_factory, rec):
    b = brain_factory(rec, answer="yes")
    b.handle("switch to windows")
    assert rec.ran("pkexec", "/usr/libexec/jarvis-os/bootnext-windows")
    rec2 = type(rec)(dict(rec.answers))
    b2 = brain_factory(rec2, answer="")                      # no answer (the pre-rc7 desktop always got this)
    assert b2.handle("switch to windows") == "Staying here, sir." and not rec2.ran("pkexec")


# ================================================================ CGNAT by voice
@pytest.mark.parametrize("text,mode", [
    ("my router uses CGNAT", "on"), ("Jarvis, my router uses CG NAT.", "on"), ("turn on cgnat mode", "on"),
    ("enable carrier-grade NAT", "on"), ("my headset is on a 100.64 address", "on"), ("cgnat on", "on"),
    ("turn off CGNAT", "off"), ("my router doesn't use cgnat", "off"), ("disable the vr cgnat option", "off"),
    ("is cgnat on?", "status"), ("cgnat status", "status"), ("is the C.G.N.A.T. option on", "status"),
    ("pair my headset", None), ("what's the weather", None), ("search the web for cgnat", None)])
def test_cgnat_phrases(text, mode):
    assert P.parse_cgnat(text) == mode
    hit = B.parse_os(commands.normalize(text))
    if mode:
        assert hit == ("vr_cgnat", {"mode": mode})
    else:
        assert not hit or hit[0] != "vr_cgnat"

class _Setter:
    def __init__(self, on=False, firewall=None):
        self.on, self.calls, self.fw = on, [], firewall
    def __call__(self, br, on):
        self.calls.append(on)
        if on is None:
            return {"ok": True, "cgnat": self.on, "firewall": self.fw or ("option: on; firewall: on" if self.on else "option: off; firewall: off")}
        self.on = bool(on)
        return {"ok": True, "cgnat": self.on, "firewall": "on" if on else "off"}

def test_cgnat_on_by_voice_asks_first(brain_factory, rec):
    b = brain_factory(rec, answer="yes"); st = b.cgnat_setter = _Setter()
    said = b.handle("my router uses cgnat")
    assert st.calls == [None, True] and "100.64" in said
    b2 = brain_factory(rec, answer="no"); st2 = b2.cgnat_setter = _Setter()
    said = b2.handle("my router uses cgnat")
    assert st2.calls == [None] and "stays off" in said
    b3 = brain_factory(rec, answer=""); st3 = b3.cgnat_setter = _Setter()
    b3.handle("turn on cgnat"); assert st3.calls == [None]           # silence is no

def test_cgnat_on_when_already_on_and_off_and_status(brain_factory, rec):
    b = brain_factory(rec, answer="yes"); st = b.cgnat_setter = _Setter(on=True)
    assert "already on" in b.handle("turn on cgnat") and st.calls == [None]
    assert "is on" in b.handle("is cgnat on")
    said = b.handle("turn off cgnat"); assert st.calls[-1] is False and "usual home-network" in said
    assert "is off" in b.handle("cgnat status")
    b.cgnat_setter = _Setter(on=False, firewall="option: off; firewall: mixed")
    assert "half set" in b.handle("is cgnat on")
    assert "half set" in P.cgnat_status_words({"ok": True, "cgnat": False, "firewall": "mixed"})

def test_cgnat_question_is_plain():
    q = P.cgnat_question("sir")
    assert q.endswith("Turn it on, sir?") and "100.64" in q and not commands.is_yes(q)


# ================================================================ Fly Vault
@pytest.mark.parametrize("text,key", [
    ("open fly vault", "fly"), ("Jarvis, open the fly vault please", "fly"), ("launch flyvault", "fly"),
    ("open the fly cages", "fly"), ("check on the flies", "fly"), ("open fly fault", "fly"),
    ("open the aim trainer", "aim"), ("open reality orbs", "orbs"), ("fly to the moon", None), ("open jarvis vr", None)])
def test_fly_vault_phrases(text, key):
    assert webapps.parse_voice(text) == key

def test_fly_vault_opens_by_voice(brain_factory, rec):
    b = brain_factory(rec)
    said = b.handle("open fly vault")
    assert rec.calls[-1] == ["xdg-open", "https://jarvis-fly.sites.tab.bot/"]
    assert said == "Opening Fly Vault in your browser, sir."

def test_fly_vault_launcher_icon_and_menu():
    assert list(webapps.APPS) == ["vr", "aim", "orbs", "fly"]
    d = SF / "usr/share/applications/jarvis-fly-web.desktop"
    cp = configparser.ConfigParser(interpolation=None); cp.optionxform = str
    cp.read_string(d.read_text().split("\n# ")[0])
    e = cp["Desktop Entry"]
    assert e["Exec"] == "xdg-open https://jarvis-fly.sites.tab.bot/" and e["Icon"] == "jarvis-fly-web" and e["Type"] == "Application"
    svg = (SF / "usr/share/icons/hicolor/scalable/apps/jarvis-fly-web.svg").read_text()
    import xml.etree.ElementTree as ET
    ET.fromstring(svg)
    labels = [l for l, _, _ in workspace.webapps_menu_items()]
    assert labels[-1] == "Fly Vault" and len(labels) == 4
    assert "jarvis-fly-web" in (ROOT / "image/build_files/build.sh").read_text()


# ================================================================ search the web
@pytest.mark.parametrize("text,want", [
    ("search the web for rtx 5060 vram", ("search", "rtx 5060 vram")),
    ("Jarvis, search the web for how tall is mount everest?", ("search", "how tall is mount everest")),
    ("search online for minecraft release date", ("search", "minecraft release date")),
    ("search the internet for fortnite news", ("search", "fortnite news")),
    ("what does the web say about balatro", ("search", "balatro")),
    ("web search best budget mouse", ("search", "best budget mouse")),
    ("search the web and tell me who won the race", ("search", "who won the race")),
    ("open source two", ("open_source", 2)), ("open that source", ("open_source", 1)), ("open the third source", ("open_source", 3)),
    ("google minecraft seeds", None), ("look up the weather in paris", None), ("search for cats", None)])
def test_search_phrases(text, want):
    assert WS.parse_voice(text) == want

def test_google_and_look_up_still_open_the_browser(brain_factory, rec):
    b = brain_factory(rec)
    assert b.handle("google minecraft seeds") == "Searching, sir."

FIX = """<html><body>
<div class="result results_links result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=x">Buy now</a>
<a class="result__snippet" href="#">An advert</a></div>
<div class="result results_links web-result "><h2 class="result__title">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fgpu%3Fa%3D1&amp;rut=x">Example GPU <b>Specs</b></a></h2>
<a class="result__snippet" href="#">The Example card has <b>8 GB</b> of fast memory. It came out in spring.</a></div>
<div class="result results_links web-result "><h2 class="result__title">
<a rel="nofollow" class="result__a" href="https://www.sample-news.com/story">Sample &amp; News</a></h2>
<a class="result__snippet" href="#">A story about a different card entirely.</a></div>
</body></html>"""

PAGE = "<html><head><script>var x=1</script></head><body><nav>Home About Contact</nav>" \
       "<p>The Example card ships with 8 GB of fast GDDR7 memory on a 128-bit bus, according to the maker.</p>" \
       "<footer>Copyright notice and all the links in the world go here</footer></body></html>"

class _Resp(io.BytesIO):
    def __init__(self, b, ctype="text/html; charset=utf-8"):
        super().__init__(b.encode()); self.headers = {"Content-Type": ctype}
    def __enter__(self): return self
    def __exit__(self, *a): return False

def _opener(log, chat=None, fail=False):
    def op(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req
        log.append(url)
        if fail:
            raise OSError("network unreachable")
        if "duckduckgo.com/html" in url:
            assert b"kp=1" in req.data                         # strict safe search
            return _Resp(FIX)
        if url.endswith("/api/chat"):
            body = json.loads(req.data); chat.append(body)
            return _Resp(json.dumps({"message": {"content": "The Example card has 8 GB of GDDR7 memory [1]."}}), "application/json")
        return _Resp(PAGE)
    return op

def test_parse_results_skips_ads_and_unwraps_links():
    r = WS.parse_results(FIX)
    assert [x["url"] for x in r] == ["https://example.org/gpu?a=1", "https://www.sample-news.com/story"]
    assert r[0]["title"] == "Example GPU Specs" and r[1]["title"] == "Sample & News" and "8 GB" in r[0]["snippet"]
    assert "GDDR7" in WS.page_text(PAGE) and "Copyright" not in WS.page_text(PAGE) and "var x" not in WS.page_text(PAGE)
    assert WS.site_name("https://en.m.wikipedia.org/wiki/X") == "wikipedia.org"
    assert WS.strip_citations("It has 8 GB [1][2]. Fast [3].") == "It has 8 GB. Fast."

class _AI:
    def __init__(self, opener, ready=True):
        self.c = {"url": "http://127.0.0.1:11434"}; self.opener = opener; self._r = ready
    def ready(self): return self._r
    def model(self): return "llama3.2:3b"

def test_answer_with_the_offline_brain(tmp_path):
    log, chat = [], []
    op = _opener(log, chat)
    r = WS.WebSearch(_AI(op), opener=op, state=str(tmp_path / "s.json")).answer("how much memory does the example card have")
    assert r["ok"] and r["how"] == "brain"
    assert r["spoken"] == "The Example card has 8 GB of GDDR7 memory. That's from example.org."
    assert "[1] Example GPU Specs\n    https://example.org/gpu?a=1" in r["text"]
    sysmsg, user = chat[0]["messages"]
    assert "ONLY the numbered sources" in sysmsg["content"] and "[1] Example GPU Specs (example.org)" in user["content"]
    assert "GDDR7" in user["content"] and chat[0]["model"] == "llama3.2:3b"
    last = json.loads((tmp_path / "s.json").read_text())
    assert last["sources"][0]["url"] == "https://example.org/gpu?a=1"

def test_answer_without_the_brain_or_in_a_game_reads_the_best_summary(tmp_path):
    log = []
    op = _opener(log)
    r = WS.WebSearch(_AI(op, ready=False), opener=op, state=str(tmp_path / "s.json")).answer("example card memory")
    assert r["how"] == "snippet" and r["spoken"].startswith("The top result, from example.org, says: The Example card has 8 GB")
    assert not any(u.endswith("/api/chat") for u in log) and not any("example.org/gpu" in u for u in log)
    r = WS.WebSearch(_AI(op), in_game=lambda: True, opener=op, state=str(tmp_path / "s.json")).answer("example card memory")
    assert r["how"] == "snippet" and "after your game" in r["spoken"] and not any(u.endswith("/api/chat") for u in log)

def test_refusals_and_offline():
    r = WS.WebSearch(None, opener=_opener([], fail=True)).answer("nude pictures")
    assert r["how"] == "refused" and "won't search" in r["spoken"]
    r = WS.WebSearch(None, opener=_opener([], fail=True)).answer("rtx 5060 memory")
    assert r["how"] == "offline" and not r["ok"]

def test_brain_web_search_speaks_notifies_and_opens_sources(brain_factory, rec, tmp_path):
    b = brain_factory(rec)
    log, chat = [], []
    op = _opener(log, chat)
    b.search_state = str(tmp_path / "last.json")
    b.searcher = WS.WebSearch(_AI(op), opener=op, state=b.search_state)
    said = b.handle("search the web for how much memory the example card has")
    assert b.spoken[-2] == "Searching the web, sir." and said.endswith("That's from example.org.")
    note = [c for c in rec.calls if c[0] == "notify-send"][-1]
    assert "JARVIS search: how much memory the example card has" in note and "https://example.org/gpu?a=1" in note[-1]
    assert b.handle("open source two") == "Opening source 2, from sample-news.com, sir."
    assert rec.calls[-1] == ["xdg-open", "https://www.sample-news.com/story"]
    assert "only had 2 sources" in b.handle("open the fifth source")

def test_brain_web_search_offline_opens_the_browser(brain_factory, rec, tmp_path):
    b = brain_factory(rec)
    opened = []
    b.hands.open_uri = opened.append
    b.searcher = WS.WebSearch(None, opener=_opener([], fail=True), state=str(tmp_path / "x.json"))
    said = b.handle("search the web for rtx 5060 memory")
    assert "opened it in your browser" in said and opened == ["https://search.brave.com/search?q=rtx+5060+memory"]

def test_open_source_before_any_search(brain_factory, rec, tmp_path):
    b = brain_factory(rec); b.search_state = str(tmp_path / "none.json")
    assert "haven't searched" in b.handle("open source one")
