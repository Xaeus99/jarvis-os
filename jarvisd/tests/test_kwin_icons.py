"""Window list via KWin (jarvisd/kwin.py), Tab's sysinfo/focus actions, and quick-launch tile icons."""
import json, os, subprocess, sys
import pytest
from jarvisd import kwin, tablink, workspace

WINS = [{"title": "Steam", "app": "steam", "pid": 11, "minimized": False, "active": False, "id": "a"},
        {"title": "Discord", "app": "discord", "pid": 14, "minimized": False, "active": True, "id": "c"},
        {"title": "OBS 31.0", "app": "com.obsproject.Studio", "pid": 12, "minimized": True, "active": False, "id": "b"}]

def fake_run(payload, rc=0):
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, rc, stdout=json.dumps(payload) + "\n", stderr="")
    run.calls = calls
    return run

def test_script_source_is_one_shot_and_quoted():
    s = kwin.script_source("org.jarvisos.WindowList.p1_x", "focus", title='Evil"); quit(); ("', app="OBS")
    assert 'callDBus("org.jarvisos.WindowList.p1_x", "/", "org.jarvisos.WindowSink", "windows"' in s
    assert 'windowList' in s and 'clientList' in s                 # Plasma 6 and 5 names
    assert '"title": "evil\\"); quit(); (\\""' in s              # a title can't break out of the JSON literal

def test_parse_puts_active_first():
    d = kwin.parse(json.dumps({"windows": WINS, "focused": None}))
    assert [w["title"] for w in d["windows"]] == ["Discord", "Steam", "OBS 31.0"]

def test_list_active_focus_through_the_helper():
    run = fake_run({"windows": WINS, "focused": None})
    assert [w["title"] for w in kwin.list_windows(run)] == ["Steam", "Discord", "OBS 31.0"]
    assert run.calls[0][1:] == ["-m", "jarvisd.kwin", "list"]
    assert kwin.active_window(run)["title"] == "Discord"
    run2 = fake_run({"windows": WINS, "focused": "OBS 31.0"})
    assert kwin.focus(app="obs", run=run2) == "OBS 31.0"
    assert run2.calls[0][-3:] == ["focus", "--app", "obs"]
    with pytest.raises(LookupError):
        kwin.focus(title="nope", run=fake_run({"windows": WINS, "focused": None}))
    with pytest.raises(ValueError):
        kwin.focus()

def test_unavailable_is_a_clean_error():
    with pytest.raises(kwin.KWinUnavailable, match="KWin isn't running"):
        kwin.list_windows(fake_run({"error": "KWin isn't running (not a Plasma session?)"}, rc=3))
    def boom(argv, **kw): raise subprocess.TimeoutExpired(argv, 8)
    with pytest.raises(kwin.KWinUnavailable):
        kwin.list_windows(boom)

def test_helper_without_kwin_reports_not_crashes():
    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent/bus", QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-m", "jarvisd.kwin", "list"], capture_output=True, text=True, timeout=60,
                       env=env, cwd=os.path.dirname(os.path.dirname(kwin.__file__)))
    assert p.returncode == 3 and "error" in json.loads(p.stdout.strip().splitlines()[-1])

def test_sysinfo_lists_windows_or_falls_back_to_processes():
    txt = tablink.sysinfo_text(["window", "apps"], windows=lambda: kwin.parse({"windows": WINS})["windows"])
    assert "Active window: Discord (discord)" in txt
    assert "  com.obsproject.Studio: OBS 31.0 [minimized]" in txt
    def none(): raise kwin.KWinUnavailable("KWin didn't answer")
    txt2 = tablink.sysinfo_text(["window", "apps"], windows=none, procs=lambda: ["kwin_wayland", "steam"])
    assert "Running processes (no window list: KWin didn't answer): kwin_wayland, steam" in txt2
    assert "unknown" in txt2
    assert "RAM" in tablink.sysinfo_text("cpu")

def test_action_runner_sysinfo_and_focus(monkeypatch):
    class H:
        def run(self, argv, **kw): return (0, "")
    monkeypatch.setattr(kwin, "list_windows", lambda run=None: WINS)
    monkeypatch.setattr(kwin, "focus", lambda title=None, app=None, run=None: "Discord" if (app or title) else None)
    r = tablink.ActionRunner(H(), lambda t: None)
    out = r.run({"mid": "m", "actions": [{"type": "sysinfo", "what": ["apps"]}, {"type": "focus_window", "process": "discord"}]})
    assert out["status"] == "ok"
    assert "Discord" in out["results"][0]["note"] and out["results"][1]["note"] == "focused: Discord"
    assert out["results"][1]["type"] == "focus_window"

# ---------------------------------------------------------------- tile icons
def _desktop(d, name, icon):
    os.makedirs(os.path.join(d, "applications"), exist_ok=True)
    open(os.path.join(d, "applications", name), "w").write(
        "[Desktop Entry]\nName=X\nIcon=%s\n\n[Desktop Action new]\nIcon=wrong\n" % icon)

def test_icon_candidates(tmp_path):
    d = str(tmp_path / "share"); _desktop(d, "com.brave.Browser.desktop", "com.brave.Browser"); _desktop(d, "mygame.desktop", "/opt/g/icon.png")
    c = lambda t: workspace.icon_candidates({"target": t}, dirs=[d], home=str(tmp_path))
    assert c("brave")[0] == "com.brave.Browser"
    assert c("files")[:2] == ["org.kde.dolphin", "system-file-manager"]
    assert c("mygame.desktop") == ["/opt/g/icon.png"]
    assert c("https://www.youtube.com")[0] == "youtube"
    assert c("https://example.com")[0] == "internet-web-browser"
    assert c("steam://rungameid/105600") == ["steam"]            # not installed: Steam's own icon
    cache = tmp_path / ".local/share/Steam/appcache/librarycache/105600"; cache.mkdir(parents=True)
    (cache / "big.jpg").write_bytes(b"x" * 5000); (cache / "small.jpg").write_bytes(b"x" * 900)
    assert c("steam://rungameid/105600")[0].endswith("small.jpg")
    assert c(str(tmp_path)) == ["folder"]
    assert c(str(tmp_path / "a.txt")) == [str(tmp_path / "a.txt") + "#file"]
    assert c("") == [] and c("unknownapp") == ["unknownapp"]

def test_icon_for_returns_real_icons_or_none(tmp_path):
    from PySide6 import QtCore, QtGui, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    png = tmp_path / "i.png"
    img = QtGui.QImage(16, 16, QtGui.QImage.Format_ARGB32); img.fill(QtGui.QColor("red")); img.save(str(png))
    _desktop(str(tmp_path / "share"), "game.desktop", str(png))
    os.environ["XDG_DATA_HOME"] = str(tmp_path / "share")
    try:
        icon_for = workspace.make_icon_for(QtGui, QtWidgets, QtCore)
        assert icon_for({"target": "game.desktop"}) is not None
        assert icon_for({"target": "definitely-not-an-icon-xyz"}) is None
        assert icon_for({"target": ""}) is None
    finally:
        del os.environ["XDG_DATA_HOME"]
