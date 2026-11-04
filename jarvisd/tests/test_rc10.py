"""rc10: data charts by voice (charts.py + chartwin.py, ported from Windows JARVIS 7.9.3: real numbers only, offline set +
World Bank / OWID / BLS / FRED live, a World Bank fallback for unemployment), a short synthesized sound per colour protocol
(protosound.py; only with the effect, never in a game, "protocol sounds off"), and your own protocols from
~/.config/jarvis/protocols.json ("midnight protocol")."""
import json, os, wave
import pytest
from jarvisd import charts as C, chartwin as CW, protocols as P, protosound as S, brain as B

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ================================================================ chart phrases
@pytest.mark.parametrize("text,kind", [
    ("jarvis, chart US unemployment since 2000", "chart"),
    ("graph the price of milk since 1920", "chart"),
    ("show me a chart of world population", "chart"),
    ("how has the price of gas changed since 1990", "chart"),
    ("compare eggs and bread", "chart"),
    ("save this chart", "save"),
    ("make it a bar chart", "mod"),
    ("adjust for inflation", "mod"),
    ("show the original prices", "mod"),
    ("close the chart", "close"),
])
def test_chart_phrases(text, kind):
    r = C.parse_voice(text)
    assert r is not None and r[0] == kind

@pytest.mark.parametrize("text", ["plot twist", "what's the plot of the movie", "don't chart that", "why didn't the chart open",
                                  "purple protocol", "search the web for milk prices", "open the chart app it"])
def test_not_chart_phrases(text):
    assert C.parse_voice(text) is None

def test_mod_payloads_and_compare_flag():
    assert C.parse_voice("make it a bar chart")[1] == {"kind": "bar"}
    assert C.parse_voice("adjust for inflation")[1] == {"inflation": True}
    assert C.parse_voice("take off the inflation adjustment")[1] == {"inflation": False}
    assert C.parse_voice("chart world population")[2] is False
    assert C.parse_voice("compare eggs and bread")[2] is True


# ================================================================ data: real numbers only, offline set, fallbacks
def test_offline_milk_chart_needs_no_network():
    def no_net(*a, **k):
        raise OSError("offline")
    ch = C.build("price of milk since 1920", opener=no_net)
    assert ch["series"] and all(C.is_money(s) for s in ch["series"])
    assert min(min(s["values"]) for s in ch["series"]) >= 1980          # nothing invented before the data starts
    assert "sir" in C.summary(ch) and "Bureau of Labor Statistics" in C.source_line(ch)

def test_offline_inflation_uses_the_cpi_table():
    nom = C.build("price of bread", opener=lambda *a, **k: (_ for _ in ()).throw(OSError()))
    real = C.build("price of bread", inflation=True, opener=lambda *a, **k: (_ for _ in ()).throw(OSError()))
    y0 = min(nom["series"][0]["values"])
    assert real["inflation"] and real["series"][0]["values"][y0] > nom["series"][0]["values"][y0]

def _fake_fetcher(ok_kinds):
    calls = []
    def f(ref, country=None, start=None, end=None, opener=None, cur=None):
        calls.append(ref[0])
        if ref[0] not in ok_kinds:
            raise C.NoData("the labour statistics service said no (daily threshold)")
        return {"title": ref[2], "unit": ref[3], "values": {2000: 4.0, 2010: 9.6, 2020: 8.1, 2024: 4.0},
                "url": "https://data.worldbank.org", "source": "World Bank, World Development Indicators (" + ref[1] + ")"}
    return f, calls

def test_unemployment_falls_back_to_the_world_bank_when_bls_says_no():
    f, calls = _fake_fetcher({"wb"})
    ch = C.build("US unemployment since 2000", fetcher=f)
    assert calls == ["bls", "wb"] and len(ch["series"]) == 1
    assert "World Bank" in C.source_line(ch)

def test_fallback_is_skipped_when_the_main_source_answers():
    f, calls = _fake_fetcher({"bls", "wb"})
    ch = C.build("US unemployment since 2000", fetcher=f)
    assert calls == ["bls"] and len(ch["series"]) == 1

def test_other_countries_unemployment_uses_the_fallback_only():
    f, calls = _fake_fetcher({"wb"})
    C.build("unemployment in germany since 2000", fetcher=f)
    assert calls == ["wb"]

def test_nothing_solid_means_nothing_drawn():
    f, _ = _fake_fetcher(set())
    with pytest.raises(C.NoData):
        C.build("US unemployment since 2000", fetcher=f)

def test_json_round_trip_keeps_year_numbers(tmp_path):
    ch = C.build("price of eggs", opener=lambda *a, **k: (_ for _ in ()).throw(OSError()))
    C.save_last(ch, "price of eggs", {"kind": "bar"}, home=str(tmp_path))
    last = C.load_last(home=str(tmp_path))
    assert last["query"] == "price of eggs" and last["opts"] == {"kind": "bar"}
    assert all(isinstance(y, int) for y in last["chart"]["series"][0]["values"])


# ================================================================ rendering
def test_render_png_and_chartwin_offscreen(tmp_path):
    pytest.importorskip("PySide6")
    ch = C.build("price of gas", opener=lambda *a, **k: (_ for _ in ()).throw(OSError()))
    p = C.save_last(ch, "price of gas", {}, home=str(tmp_path))
    out = tmp_path / "w.png"
    assert CW.main([p, "--png", str(out), "--size", "900x520"]) == 0
    from PySide6 import QtGui
    img = QtGui.QImage(str(out))
    assert img.width() == 900 and img.height() == 520
    assert CW.saved_name(ch, str(tmp_path)).endswith(".png")

def test_window_argv_runs_the_module():
    a = C.window_argv("/x/last.json")
    assert a[1:] == ["-m", "jarvisd.chartwin", "/x/last.json"]


# ================================================================ brain wiring
def _brain(tmp_path, gaming=False):
    said, ran, snd = [], [], []
    b = B.LinuxBrain({}, state_dir=str(tmp_path / "s"), run=lambda argv, **k: ran.append(argv) or (0, ""),
                     say=lambda t: said.append(t) or t)
    b.cfg_path = str(tmp_path / "config.json"); b.home = str(tmp_path)
    b.ui_send = lambda name, msg: {"ok": True, "visible": True}
    b.is_gaming = lambda: gaming
    b.protocol_sound = lambda th: snd.append(th)
    return b, said, ran, snd

def test_brain_chart_then_bar_then_save(tmp_path, monkeypatch):
    real = C.build
    monkeypatch.setattr(C, "build", lambda q, **k: real(q, opener=lambda *a, **kk: (_ for _ in ()).throw(OSError()), **k))
    b, said, ran, _ = _brain(tmp_path)
    b.handle("jarvis, chart the price of milk since 1980")
    assert said[-1].startswith("Milk went from $") and ran[-1][1:3] == ["-m", "jarvisd.chartwin"]
    b.handle("make it a bar chart")
    assert said[-1] == "Bar chart, sir." and C.load_last(str(tmp_path))["chart"]["kind"] == "bar"
    b.handle("adjust for inflation")
    assert "dollars" in said[-1] and C.load_last(str(tmp_path))["opts"] == {"kind": "bar", "inflation": True}
    pytest.importorskip("PySide6")
    b.handle("save this chart")
    assert said[-1] == "Saved to Pictures, in JARVIS Charts, sir."
    assert os.listdir(os.path.join(str(tmp_path), "Pictures", "JARVIS Charts"))

def test_brain_inflation_on_a_non_money_chart(tmp_path, monkeypatch):
    f, _ = _fake_fetcher({"bls"})
    real = C.build
    monkeypatch.setattr(C, "build", lambda q, **k: real(q, fetcher=f, **k))
    b, said, _, _ = _brain(tmp_path)
    b.handle("chart US unemployment since 2000")
    b.handle("adjust for inflation")
    assert said[-1] == "That chart isn't in dollars, sir, so there's nothing to adjust for inflation."

def test_brain_no_chart_yet(tmp_path):
    b, said, _, _ = _brain(tmp_path)
    b.handle("save this chart")
    assert said[-1].startswith("There's no chart to save yet")
    b.handle("make it a bar chart")
    assert said[-1].startswith("There's no chart up yet")

def test_brain_no_data_is_said_not_drawn(tmp_path, monkeypatch):
    f, _ = _fake_fetcher(set())
    real = C.build
    monkeypatch.setattr(C, "build", lambda q, **k: real(q, fetcher=f, **k))
    b, said, ran, _ = _brain(tmp_path)
    b.handle("chart US unemployment since 2000")
    assert "won't" in said[-1] and not ran


# ================================================================ protocol sounds
def test_synth_is_short_loud_enough_and_deterministic():
    a, b2 = S.synth("vibranium"), S.synth("vibranium")
    assert a.dtype.name == "int16" and len(a) == int(1.5 * S.SR)
    assert (a == b2).all() and abs(int(a.max())) > 8000
    assert not (S.synth("ultron") == a).all()
    assert int(abs(S.synth("cyan", volume=0)).max()) == 0

def test_wav_is_cached(tmp_path):
    p = S.wav_path("cyan", 60, home=str(tmp_path))
    with wave.open(p) as w:
        assert w.getframerate() == S.SR and w.getnchannels() == 1
    m = os.path.getmtime(p)
    assert S.wav_path("cyan", 60, home=str(tmp_path)) == p and os.path.getmtime(p) == m

def test_play_respects_the_switch_and_uses_a_player(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(S, "players", lambda: [["pw-play"]])
    assert S.play("cyan", {"protocol_sound": False}, home=str(tmp_path), popen=lambda *a, **k: started.append(a)) is None
    p = S.play("cyan", {}, home=str(tmp_path), popen=lambda argv, **k: started.append(argv))
    assert p and started[-1] == ["pw-play", p]
    monkeypatch.setattr(S, "players", lambda: [])
    assert S.play("cyan", {}, home=str(tmp_path), popen=lambda *a, **k: started.append(a)) is None

def test_protocol_plays_its_sound_only_with_the_effect(tmp_path):
    b, said, _, snd = _brain(tmp_path)
    json.dump({"theme": "cyan"}, open(b.cfg_path, "w"))
    b.handle("purple protocol"); b.handle("normal protocol")
    assert snd == ["vibranium", "cyan"]
    b.handle("red theme")                                   # a quiet recolour: no sound
    assert snd == ["vibranium", "cyan"]
    g, _, _, gsnd = _brain(tmp_path / "g", gaming=True)
    json.dump({"theme": "cyan"}, open(g.cfg_path, "w"))
    g.handle("purple protocol")
    assert gsnd == []                                       # never during a game

def test_protocol_sounds_off_by_voice(tmp_path):
    b, said, _, _ = _brain(tmp_path)
    b.handle("protocol sounds off")
    assert said[-1] == "Protocol sounds off, sir." and json.load(open(b.cfg_path))["protocol_sound"] is False
    b.handle("turn on protocol sounds")
    assert json.load(open(b.cfg_path))["protocol_sound"] is True

@pytest.mark.parametrize("text,want", [("protocol sounds off", ("sound", False)), ("mute protocol sounds", ("sound", False)),
                                       ("unmute the protocol sound", ("sound", True)), ("list my protocols", ("list", None)),
                                       ("what protocols do you have", ("list", None)), ("purple protocol", None)])
def test_parse_extra(text, want):
    assert P.parse_extra(text) == want


# ================================================================ your own protocols
def test_custom_protocols_file(tmp_path):
    f = tmp_path / "protocols.json"
    json.dump({"_help": "x", "Midnight": "stealth", "storm": "blue", "bad": "not a colour", "what": "red",
               "normal": "red", "three word name": "red", "n": 5}, open(f, "w"))
    m = P.load_custom(str(f))
    assert m == {"midnight": "stealth", "storm": "cyan"}
    assert P.parse_voice("midnight protocol", custom_path=str(f)) == ("engage", "midnight", "stealth")
    assert P.parse_voice("engage storm protocol", custom_path=str(f)) == ("engage", "storm", "cyan")
    assert P.parse_voice("purple protocol", custom_path=str(f)) == ("engage", "purple", "vibranium")
    assert P.parse_voice("midnight protocol", custom_path=str(tmp_path / "none.json"))[0] == "unknown"

def test_example_file_written_once(tmp_path):
    f = tmp_path / "jarvis" / "protocols.json"
    assert P.ensure_custom_example(str(f)) is True
    json.dump({"mine": "red"}, open(f, "w"))
    assert P.ensure_custom_example(str(f)) is False and json.load(open(f)) == {"mine": "red"}
    assert P.load_custom(str(P.CUSTOM_PATH)) is not None

def test_list_words_names_yours(tmp_path):
    f = tmp_path / "p.json"; json.dump({"midnight": "stealth"}, open(f, "w"))
    assert P.list_words("sir", str(f)).endswith("plus yours: midnight. Normal protocol puts the usual look back.")
    assert "protocols.json" in P.unknown_words("green")
