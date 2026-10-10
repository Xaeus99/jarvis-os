import json, os, stat, tempfile, time
import numpy as np
import pytest
from jarvisd import workspace, ipc
from jarvisd.core import wsdata

def _app():
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

def test_full_workspace_renders_and_toggles(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    from PySide6 import QtCore, QtWidgets
    from jarvisd.core import workspace as W
    app = _app()
    win = workspace.build_window({"briefing": {}}, QtWidgets, QtCore)
    assert isinstance(win, W.Workspace)                       # the real v4 panel, not the placeholder
    c = workspace.Controller(win)
    assert c.handle({"cmd": "toggle"})["visible"] is True
    end = time.time() + 1.5
    while time.time() < end:
        app.processEvents(); time.sleep(0.02)
    img = win.grab()
    assert img.width() >= 1000
    out = os.environ.get("JARVIS_SHOT")
    if out:
        img.save(out)
    arr = img.toImage(); assert arr.pixelColor(arr.width() // 2, arr.height() // 2).isValid()
    assert c.handle({"cmd": "toggle"})["visible"] is False   # closing only hides; the process stays for the next press
    for p in win._pollers:
        p.stop.set()

def test_hud_mirror_logs_the_conversation():
    class W:
        def __init__(self): self.log = []; self.state = None
        def set_state(self, s): self.state = s
        def set_level(self, v): pass
        def add_log(self, who, text): self.log.append((who, text))
    st = {"state": "idle", "heard": "", "caption": "", "heard_ts": 0, "caption_ts": 0}
    w = W(); m = workspace.HudMirror(w, lambda: dict(st))
    m.tick()
    st.update(state="thinking", heard="clip that", heard_ts=1); m.tick()
    st.update(state="speaking", caption="Clipped, sir.", caption_ts=2); m.tick(); m.tick()
    assert w.log == [("you", "clip that"), ("jarvis", "Clipped, sir.")] and w.state == "speaking"

def test_launch_targets():
    assert workspace.launch_argv("discord") == ["flatpak", "run", "com.discordapp.Discord"]
    assert workspace.launch_argv("https://www.youtube.com") == ["xdg-open", "https://www.youtube.com"]
    assert workspace.launch_argv("steam://rungameid/12345") == ["xdg-open", "steam://rungameid/12345"]
    assert workspace.launch_argv("/usr/share/applications/org.kde.dolphin.desktop") == ["gtk-launch", "org.kde.dolphin"]
    assert workspace.launch_argv("/bin/true") == ["/bin/true"]
    assert workspace.launch_argv("prismlauncher") == ["gtk-launch", "prismlauncher"]
    assert workspace.launch_argv("") is None

def test_save_cfg_keeps_other_keys(tmp_path):
    p = str(tmp_path / "config.json")
    json.dump({"forwarding": {"to": "x"}, "theme": "cyan"}, open(p, "w"))
    workspace.save_cfg(lambda c: c.__setitem__("theme", "vibranium"), p)
    workspace.save_cfg(lambda c: c.setdefault("workspace", {}).__setitem__("quick_launch", [{"name": "A", "target": "a"}]), p)
    c = json.load(open(p))
    assert c == {"forwarding": {"to": "x"}, "theme": "vibranium", "workspace": {"quick_launch": [{"name": "A", "target": "a"}]}}

def test_gpu_nvidia_smi(tmp_path):
    smi = tmp_path / "nvidia-smi"
    smi.write_text("#!/bin/sh\necho '37, 61, 2048, 12288, NVIDIA GeForce RTX 4070'\n"); smi.chmod(0o755)
    g = wsdata.Gpu(smi=str(smi)).read()
    assert g == {"gpu": 37.0, "gpu_temp": 61.0, "vram_used_gb": 2.0, "vram_total_gb": 12.0, "gpu_name": "NVIDIA GeForce RTX 4070"}

def test_gpu_amd_sysfs(tmp_path):
    dev = tmp_path / "card1" / "device"; (dev / "hwmon" / "hwmon3").mkdir(parents=True)
    (dev / "gpu_busy_percent").write_text("42\n"); (dev / "mem_info_vram_used").write_text(str(3 * 2**30))
    (dev / "mem_info_vram_total").write_text(str(16 * 2**30)); (dev / "hwmon" / "hwmon3" / "temp1_input").write_text("55000\n")
    (tmp_path / "card1-DP-1").mkdir()
    g = wsdata.Gpu(drm_root=str(tmp_path), smi="").read()
    assert g == {"gpu": 42.0, "vram_used_gb": 3.0, "vram_total_gb": 16.0, "gpu_temp": 55.0}

def test_gpu_none_is_empty(tmp_path):
    assert wsdata.Gpu(drm_root=str(tmp_path), smi="").read() == {}

def test_stats_sample_on_linux():
    s = wsdata.Stats(); s.gpu = wsdata.Gpu(drm_root="/nonexistent", smi="")
    d = s.sample()
    assert 0 <= d["cpu"] <= 100 and d["ram_total_gb"] > 0 and d["disk_free_gb"] > 0 and d["disk_name"] == ""

def test_now_playing_from_mpris():
    out = "Playing\tSong\tArtist\tAlbum\tspotify\t61000000\t200000000\n"
    assert wsdata.parse_playerctl(out) == {"title": "Song", "artist": "Artist", "album": "Album", "playing": True,
                                           "app": "Spotify", "position": 61.0, "duration": 200.0}
    assert wsdata.now_playing(run=lambda argv: (1, "No players found")) == {}
    assert wsdata.parse_playerctl("Stopped\t\t\t\tfirefox\t\t") == {}

def test_clip_folders_and_recent_clips(tmp_path):
    cap = tmp_path / "Videos" / "Captures"; cap.mkdir(parents=True)
    (cap / "Replay 1.mkv").write_bytes(b"x" * 10)
    fs = wsdata.clip_folders(home=str(tmp_path))
    assert fs == [str(cap), str(tmp_path / "Videos")]
    clips = wsdata.recent_clips(fs, limit=6)
    assert [os.path.basename(c[0]) for c in clips] == ["Replay 1.mkv"]

def test_daemon_listen_button_forces_a_command():
    from jarvisd.daemon import Daemon, CHUNK, RATE
    class Src:
        def __init__(self): self.n = 0
        def read(self):
            self.n += 1
            if self.n > 60: return None
            return np.zeros(CHUNK, np.int16) if self.n < 3 or self.n > 20 else (3000 * np.sin(np.arange(CHUNK))).astype(np.int16)
    class Wake:
        def score(self, c): return False, None
        def reset(self): pass
    class B:
        sir = "sir"; got = []
        def handle(self, t): self.got.append(t)
        def say(self, t): pass
    d = Daemon(Src(), Wake(), lambda a: "what time is it", B())
    d.force.set()
    assert d.run() == 1 and B.got == ["what time is it"] and not d.force.is_set()
