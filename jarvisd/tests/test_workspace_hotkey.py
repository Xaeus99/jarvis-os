import json, os, tempfile, threading, time
import pytest
from jarvisd import ipc, workspace, cli

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "image", "system_files")

@pytest.fixture
def rt(monkeypatch):
    d = tempfile.mkdtemp(prefix="jv", dir="/tmp")          # short: Unix socket paths max out at 107 bytes
    monkeypatch.setenv("XDG_RUNTIME_DIR", d)
    return d

class Win:
    def __init__(self): self.vis = False; self.min = False; self.logs = []
    def isVisible(self): return self.vis
    def isMinimized(self): return self.min
    def open(self): self.vis = True; self.min = False
    def close(self): self.vis = False
    def add_log(self, who, text): self.logs.append((who, text))

def test_ipc_roundtrip_and_single_instance(rt):
    srv = ipc.Server("t", lambda m: {"echo": m["x"]}).start()
    try:
        assert ipc.send("t", {"x": 5}) == {"echo": 5}
        assert ipc.alive("t")
        assert oct(os.stat(ipc.sock_path("t")).st_mode & 0o777) == "0o600"
        with pytest.raises(RuntimeError):
            ipc.Server("t", lambda m: None).start()          # a second instance refuses to start
    finally:
        srv.close()
    assert ipc.send("t", {"x": 1}) is None                   # nothing listening -> None, never an exception

def test_stale_socket_is_replaced(rt):
    import socket
    s = socket.socket(socket.AF_UNIX); s.bind(ipc.sock_path("t")); s.close()   # left behind by a crash
    srv = ipc.Server("t", lambda m: {"ok": True}).start()
    try:
        assert ipc.send("t", {"cmd": "x"}) == {"ok": True}
    finally:
        srv.close()

def test_bad_message_does_not_kill_server(rt):
    def boom(m): raise ValueError("nope")
    srv = ipc.Server("t", boom).start()
    try:
        assert ipc.send("t", {"cmd": "x"})["ok"] is False
        assert ipc.alive("t")
    finally:
        srv.close()

def test_controller_toggles():
    w = Win(); c = workspace.Controller(w)
    assert c.handle({"cmd": "toggle"})["visible"] is True
    assert c.handle({"cmd": "toggle"})["visible"] is False
    w.min = True; w.vis = True
    assert c.handle({"cmd": "toggle"})["visible"] is True     # minimized counts as hidden: toggle restores it
    assert c.handle({"cmd": "close"})["visible"] is False
    c.handle({"cmd": "log", "who": "you", "text": "hi"}); assert w.logs == [("you", "hi")]
    assert c.handle({"cmd": "dance"})["ok"] is False

def test_second_toggle_goes_to_running_panel(rt):
    w = Win(); srv = ipc.Server(workspace.NAME, workspace.Controller(w).handle).start()
    started = []
    try:
        assert workspace.control("toggle", run=lambda *a, **k: started.append(a) or (0, "")) == (True, "sent")
        assert workspace.control("toggle", run=lambda *a, **k: started.append(a) or (0, "")) == (False, "sent")
    finally:
        srv.close()
    assert started == []                                    # never starts a second panel

def test_toggle_starts_the_unit_when_no_panel(rt):
    calls, box = [], {}
    def run(argv, timeout=15):
        calls.append(argv)
        box["srv"] = ipc.Server(workspace.NAME, workspace.Controller(Win()).handle).start()   # "systemd started it"
        return 0, ""
    try:
        vis, how = workspace.control("toggle", run=run, sleep=lambda s: None)
    finally:
        box["srv"].close()
    assert calls == [["systemctl", "--user", "start", "jarvis-workspace.service"]]
    assert (vis, how) == (True, "started")

def test_close_when_not_running_does_not_start_anything(rt):
    assert workspace.control("close", run=lambda *a, **k: pytest.fail("started")) == (False, "not running")

def test_falls_back_to_spawn_without_systemd(rt):
    spawned = []
    vis, how = workspace.control("open", run=lambda *a, **k: (1, "no user bus"), spawn=lambda: spawned.append(1),
                                 sleep=lambda s: None, wait_s=0.05)
    assert spawned == [1] and how == "failed"

def test_desktop_file_binds_ctrl_shift_j():
    txt = open(os.path.join(ROOT, "usr/share/applications/jarvis-workspace.desktop")).read()
    assert "X-KDE-Shortcuts=Ctrl+Shift+J" in txt and "Exec=jarvisctl workspace toggle" in txt
    unit = open(os.path.join(ROOT, "usr/lib/systemd/user/jarvis-workspace.service")).read()
    assert "ExecStart=/usr/lib/jarvis-os/venv/bin/python -m jarvisd.workspace" in unit

def test_jarvisctl_workspace(monkeypatch, capsys):
    got = []
    monkeypatch.setattr(workspace, "control", lambda what, run=None: got.append(what) or (True, "sent"))
    assert cli.main(["workspace", "toggle"]) == 0 and cli.main(["workspace"]) == 0
    assert got == ["toggle", "toggle"]
    assert json.loads(capsys.readouterr().out.splitlines()[0]) == {"visible": True, "how": "sent"}

def test_voice_opens_and_closes_workspace(brain_factory, rec):
    b = brain_factory(rec); asked = []
    b.panel = lambda what: asked.append(what) or (what == "open", "sent")
    assert "Workspace online" in b.handle("Jarvis, open the workspace")
    assert "Workspace closed" in b.handle("close the workspace")
    assert asked == ["open", "close"]

def test_real_qt_panel_toggles_over_the_socket(rt):
    from PySide6 import QtCore, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = workspace.build_window({}, QtWidgets, QtCore)
    srv = ipc.Server(workspace.NAME, workspace.Controller(win, workspace.make_gui_caller(QtCore)).handle).start()
    out = []
    t = threading.Thread(target=lambda: out.extend([ipc.send(workspace.NAME, {"cmd": "toggle"}, timeout=5),
                                                    ipc.send(workspace.NAME, {"cmd": "toggle"}, timeout=5)]))
    t.start()
    end = time.time() + 8
    while t.is_alive() and time.time() < end:
        app.processEvents(); time.sleep(0.01)
    srv.close()
    assert [o["visible"] for o in out] == [True, False]
