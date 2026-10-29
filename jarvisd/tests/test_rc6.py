"""rc6: the workspace panel's PAIR VR button, the VR bridge's CGNAT option (100.64.0.0/10 home networks, off by default,
jarvisctl vr cgnat on|off|status through a fixed root helper), and launchers for JARVIS's web apps (JARVIS VR, the aim
trainer, Reality Orbs): app-menu entries, quick-launch tiles and voice phrases."""
import configparser, json, os, pathlib, re, subprocess, time, types
import xml.etree.ElementTree as ET
import pytest
from jarvisd import vrbridge as V, vrpair as P, webapps, workspace, brain as B, cli

ROOT = pathlib.Path(__file__).resolve().parents[2]
SF = ROOT / "image" / "system_files"


# ================================================================ CGNAT option
def test_cgnat_addresses_refused_by_default():
    assert V.is_lan_peer("192.168.1.20") and V.is_lan_peer("10.0.0.5") and V.is_lan_peer("172.20.3.4")
    assert V.is_lan_peer("127.0.0.1")
    assert not V.is_lan_peer("100.64.0.9") and not V.is_lan_peer("100.127.255.1")
    assert not V.is_lan_peer("8.8.8.8") and not V.is_lan_peer("169.254.1.1")
    assert V.lan_ip(connect=lambda: "100.72.10.4") is None
    assert V.lan_ip(connect=lambda: "192.168.0.7") == "192.168.0.7"

def test_cgnat_addresses_allowed_when_switched_on():
    assert V.is_lan_peer("100.64.0.9", cgnat=True) and V.is_lan_peer("100.127.255.1", cgnat=True)
    assert V.lan_ip(connect=lambda: "100.72.10.4", cgnat=True) == "100.72.10.4"
    # only that one range: neighbours and the public internet stay out
    for ip in ("100.63.255.255", "100.128.0.1", "8.8.8.8", "169.254.3.3", "1.1.1.1"):
        assert not V.is_lan_peer(ip, cgnat=True), ip
    assert V.lan_ip(connect=lambda: "100.128.0.1", cgnat=True) is None
    assert V.is_lan_peer("192.168.1.20", cgnat=True)

class _Hooks(V.Hooks):
    sir = "sir"
    def pc_name(self): return "PC"

def test_bridge_reads_the_option_and_reports_it(tmp_path):
    br = V.Bridge(str(tmp_path), _Hooks(), {}, start_threads=False)
    assert br.cgnat() is False and br.status()["cgnat"] is False
    br2 = V.Bridge(str(tmp_path), _Hooks(), {"allow_cgnat": True}, start_threads=False)
    assert br2.cgnat() is True and P.status_json(br2)["cgnat"] is True

def test_set_cgnat_runs_the_helper_and_saves_config(tmp_path):
    cfgp = tmp_path / "config.json"; cfgp.write_text(json.dumps({"theme": "red", "vr": {"port": 8771}}))
    br = V.Bridge(str(tmp_path), _Hooks(), {}, start_threads=False)
    ran = []
    def run(argv):
        ran.append(argv); return types.SimpleNamespace(returncode=0, stdout=argv[-1] + "\n", stderr="")
    r = P.set_cgnat(br, True, run=run, cfg_path=str(cfgp))
    assert r == {"ok": True, "cgnat": True, "firewall": "on"}
    assert ran == [["pkexec", "/usr/libexec/jarvis-os/vr-cgnat", "on"]]
    assert br.cgnat() is True
    c = json.loads(cfgp.read_text()); assert c["vr"] == {"port": 8771, "allow_cgnat": True} and c["theme"] == "red"
    assert "100.64" in P.cgnat_words(r)
    r = P.set_cgnat(br, False, run=run, cfg_path=str(cfgp))
    assert r["ok"] and r["cgnat"] is False and br.cgnat() is False
    assert json.loads(cfgp.read_text())["vr"]["allow_cgnat"] is False
    st = P.set_cgnat(br, None, run=run, cfg_path=str(cfgp))
    assert st["ok"] and st["firewall"] == "status" and ran[-1][-1] == "status"

def test_set_cgnat_failure_changes_nothing(tmp_path):
    cfgp = tmp_path / "config.json"
    br = V.Bridge(str(tmp_path), _Hooks(), {}, start_threads=False)
    run = lambda argv: types.SimpleNamespace(returncode=126, stdout="", stderr="Not authorized")
    r = P.set_cgnat(br, True, run=run, cfg_path=str(cfgp))
    assert r["ok"] is False and "Not authorized" in r["error"] and br.cgnat() is False and not cfgp.exists()
    assert "couldn't" in P.cgnat_words(r)

def test_cgnat_helper_script_and_polkit():
    h = SF / "usr" / "libexec" / "jarvis-os" / "vr-cgnat"
    t = h.read_text()
    assert subprocess.run(["bash", "-n", str(h)]).returncode == 0
    assert "RANGE=100.64.0.0/10" in t and "SET=jarvis-lan" in t
    # it touches exactly one range in one ipset - no ports, zones, services or other entries
    assert not re.search(r"--add-(?:port|service|rich-rule|source)|--zone|--set-default-zone|--panic", t)
    assert t.count("--add-entry=\"$RANGE\"") == 3 and t.count("--remove-entry=\"$RANGE\"") == 3
    pol = ET.parse(SF / "usr" / "share" / "polkit-1" / "actions" / "os.jarvis.vr-cgnat.policy").getroot()
    a = pol.find("action"); assert a.get("id") == "os.jarvis.vr-cgnat"
    assert a.find("annotate").text == "/usr/libexec/jarvis-os/vr-cgnat"
    assert a.find("defaults/allow_any").text == "no"
    rules = (SF / "usr" / "share" / "polkit-1" / "rules.d" / "49-jarvis-vr-cgnat.rules").read_text()
    assert 'action.id == "os.jarvis.vr-cgnat"' in rules and 'isInGroup("wheel")' in rules
    # the shipped ipset is still RFC 1918 only: CGNAT is opt-in
    ips = ET.parse(SF / "usr" / "lib" / "firewalld" / "ipsets" / "jarvis-lan.xml").getroot()
    assert "100.64.0.0/10" not in [e.text for e in ips.findall("entry")]

def test_jarvisctl_vr_cgnat(monkeypatch, capsys):
    sent = []
    def send(name, msg, timeout=2.0):
        sent.append((name, msg)); return {"ok": True, "cgnat": msg.get("on") is True, "firewall": "on" if msg.get("on") else "off"}
    from jarvisd import ipc
    monkeypatch.setattr(ipc, "send", send)
    assert cli.main(["vr", "cgnat", "on"]) == 0
    assert sent[-1] == ("daemon", {"cmd": "vr_cgnat", "on": True})
    assert "on" in capsys.readouterr().out
    assert cli.main(["vr", "cgnat", "off"]) == 0 and sent[-1][1]["on"] is False
    assert cli.main(["vr", "cgnat"]) == 0 and sent[-1][1]["on"] is None
    assert cli.main(["vr", "cgnat", "maybe"]) == 2

def test_daemon_answers_vr_cgnat():
    src = (ROOT / "jarvisd" / "jarvisd" / "daemon.py").read_text()
    assert '"vr_cgnat"' in src and "vrpair.set_cgnat" in src


# ================================================================ PAIR VR button
L = workspace.VrPairLogic

def test_vr_button_labels():
    assert L.label(None)[0] == "VR OFF"
    assert L.label({"ok": False, "running": False})[0] == "VR OFF"
    base = {"ok": True, "running": True, "paired": 0, "connected": [], "code_active": False}
    assert L.label(base)[0] == "PAIR VR" and "pair my headset" in L.label(base)[1]
    assert L.label(dict(base, code_active=True))[0] == "VR CODE LIVE"
    assert L.label(dict(base, paired=1))[0] == "VR PAIRED"
    assert L.label(dict(base, paired=1, connected=["lan"]))[0] == "VR LINKED"
    assert "home network" in L.label(dict(base, paired=1, connected=["lan"]))[1]
    assert "relay" in L.label(dict(base, paired=1, connected=["relay"]))[1]
    for st in (None, base, dict(base, paired=1, connected=["lan"])):
        assert L.label(st)[0] in L.LABELS

def test_vr_button_click_says_what_the_voice_would():
    base = {"ok": True, "running": True, "paired": 0, "connected": [], "code_active": False}
    assert L.phrase(None) is None
    assert L.phrase(base) == "pair my headset"
    assert L.phrase(dict(base, code_active=True)) == "pair my headset"
    assert L.phrase(dict(base, paired=1)) == "is my headset connected"
    # both phrases are ones the brain already routes to the VR bridge
    assert P.parse_voice(L.phrase(base)) == "pair" and P.parse_voice("is my headset connected") == "status"

def _app():
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

def test_vr_button_in_the_real_top_bar(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(workspace, "CFG_PATH", str(tmp_path / "config.json"))
    from PySide6 import QtCore, QtWidgets
    app = _app()
    status = {"ok": True, "running": True, "paired": 0, "connected": [], "code_active": False}
    sent = []
    def send(name, msg, timeout=2.0):
        sent.append((name, msg))
        return dict(status) if msg["cmd"] == "vr_status" else {"ok": True}
    from jarvisd import ipc
    monkeypatch.setattr(ipc, "send", send)
    win = workspace.build_window({"briefing": {}}, QtWidgets, QtCore)
    btn = win._vr_button
    win.resize(1500, 900); win.open()
    end = time.time() + 1.5
    while time.time() < end:
        app.processEvents(); time.sleep(0.02)
    assert btn.isVisible() and btn.text() == "PAIR VR"
    # in the top bar, left of the theme button, never over the chips (they stop at the VOICE OUT label)
    top = win.top
    assert btn.parent() is top
    assert btn.geometry().right() < top.b_theme.geometry().left()
    assert btn.geometry().left() > top.audio_label.geometry().left()
    btn.click()
    end = time.time() + 2.5
    while time.time() < end and not any(m.get("cmd") == "say" for _, m in sent):
        app.processEvents(); time.sleep(0.02)
    assert ("daemon", {"cmd": "say", "text": "pair my headset"}) in sent
    status.update(paired=1, connected=["lan"])
    end = time.time() + 6
    while time.time() < end and btn.text() != "VR LINKED":
        app.processEvents(); time.sleep(0.05)
    assert btn.text() == "VR LINKED"
    # the JARVIS APPS menu sits in the Quick launch header, left of EDIT, and launches through xdg-open
    wb = win._webapps_button
    assert wb.parent() is win.p_launch and wb.isVisible()
    assert wb.geometry().right() < win.p_launch.b_edit.geometry().left()
    launched = []
    monkeypatch.setattr(workspace, "detached", lambda argv: launched.append(argv))
    acts = win._webapps_menu.actions()
    assert [a.text() for a in acts] == ["JARVIS VR", "Aim Trainer", "Reality Orbs"]
    acts[1].trigger()
    assert launched == [["xdg-open", "https://jarvis-aim.sites.tab.bot/"]]
    out = os.environ.get("JARVIS_SHOT")
    if out:
        win.grab().save(out.replace(".png", "_vr.png"))
    win.close()
    for p in win._pollers:
        p.stop.set()


# ================================================================ web-app launchers
def test_web_apps_and_urls():
    assert set(webapps.APPS) == {"vr", "aim", "orbs"}
    for k, a in webapps.APPS.items():
        assert re.fullmatch(r"https://jarvis-(vr|aim|orbs)\.sites\.tab\.bot/", a["url"]), a["url"]
        assert webapps.open_argv(k) == ["xdg-open", a["url"]]
    assert webapps.open_argv("nope") is None

@pytest.mark.parametrize("said,key", [
    ("open the aim trainer", "aim"), ("Jarvis, launch aim trainer please", "aim"), ("practice my aim", "aim"),
    ("open jarvis aim trainer", "aim"), ("start aim training", "aim"),
    ("open reality orbs", "orbs"), ("launch the orbs", "orbs"), ("turn my room into another world", "orbs"),
    ("open the reality orbs app", "orbs"),
])
def test_web_app_phrases(said, key):
    assert webapps.parse_voice(said) == key

@pytest.mark.parametrize("said", ["what is aim", "open steam", "pair my headset", "open jarvis vr", "aim for the stars"])
def test_web_app_phrases_leave_others_alone(said):
    assert webapps.parse_voice(said) is None

def test_brain_opens_a_web_app(rec, brain_factory):
    br = brain_factory(rec)
    br.handle("open the aim trainer")
    assert rec.ran("xdg-open", "https://jarvis-aim.sites.tab.bot/")
    assert any("aim trainer" in s.lower() for s in br.spoken)
    br.handle("open reality orbs")
    assert rec.ran("xdg-open", "https://jarvis-orbs.sites.tab.bot/")
    # "open jarvis vr" stays with the VR bridge's phrase (notification with the address), not a second route
    assert B.parse_os("open jarvis vr")[0] == "vr_link"
    assert B.parse_os("open the aim trainer") == ("web_app", {"app": "aim"})

def test_desktop_entries():
    for k, a in webapps.APPS.items():
        f = SF / "usr" / "share" / "applications" / a["desktop"]
        cp = configparser.ConfigParser(interpolation=None); cp.optionxform = str; cp.read(f)
        d = cp["Desktop Entry"]
        assert d["Type"] == "Application" and d["Exec"] == "xdg-open " + a["url"]
        assert d["Icon"] == a["desktop"][:-8] and d["Categories"].endswith(";")
        assert d["Name"].startswith("J.A.R.V.I.S.") and "NoDisplay" not in d
        svg = SF / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps" / (d["Icon"] + ".svg")
        root = ET.parse(svg).getroot(); assert root.tag.endswith("svg") and root.get("viewBox") == "0 0 128 128"
        assert "<image" not in svg.read_text() and "href" not in svg.read_text()       # original vector art, nothing embedded

def test_webapps_menu_items():
    items = workspace.webapps_menu_items()
    assert [i[0] for i in items] == ["JARVIS VR", "Aim Trainer", "Reality Orbs"]
    for label, icon, argv in items:
        assert icon.startswith("jarvis-") and icon.endswith("-web")
        assert argv[0] == "xdg-open" and argv[1].startswith("https://jarvis-")


def test_build_checks_cgnat_off_and_launchers():
    sh = (ROOT / "image" / "build_files" / "build.sh").read_text()
    assert "firewall-offline-cmd --ipset=jarvis-lan --get-entries" in sh
    assert 'CGNAT (100.64.0.0/10) must be off by default' in sh
    assert "test -x /usr/libexec/jarvis-os/vr-cgnat" in sh
    for f in ("jarvis-vr-web", "jarvis-aim-web", "jarvis-orbs-web"):
        assert f in sh
