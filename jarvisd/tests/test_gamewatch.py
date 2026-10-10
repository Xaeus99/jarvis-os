import json, os
from jarvisd import gamewatch, hud

def fake_proc(root, pid, name, cmd="", env=None):
    d = root / str(pid); d.mkdir(parents=True)
    (d / "comm").write_text(name + "\n")
    (d / "cmdline").write_bytes(b"\0".join(x.encode() for x in cmd.split()) + b"\0")
    (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in (env or {"HOME": "/home/u"}).items()) + b"\0")

def test_steam_game_found_by_environment(tmp_path):
    fake_proc(tmp_path, 100, "steam", "/home/u/.local/share/Steam/ubuntu12_32/steam", {"SteamAppId": "0"})
    fake_proc(tmp_path, 101, "steamwebhelper", "steamwebhelper", {"SteamAppId": "0"})
    assert gamewatch.detect({}, None, str(tmp_path))["gaming"] is False
    fake_proc(tmp_path, 200, "Game.x86_64", "/games/common/Game/Game.x86_64", {"SteamAppId": "123450", "SteamGameId": "123450"})
    d = gamewatch.detect({}, None, str(tmp_path))
    assert d["gaming"] and d["why"] == "steam" and d["appid"] == "123450" and d["pid"] == 200

def test_proton_game_and_reaper(tmp_path):
    procs = [(5, "reaper", "reaper SteamLaunch AppId=2767030 -- /proton waitforexitandrun game.exe", {})]
    assert gamewatch.classify(procs)["appid"] == "2767030"
    procs = [(6, "game.exe", "Z:\\game.exe", {"STEAM_COMPAT_APP_ID": "2357570"})]
    assert gamewatch.classify(procs)["gaming"]

def test_runtime_and_tools_are_not_games():
    procs = [(5, "reaper", "reaper SteamLaunch AppId=1628350 -- pressure-vessel", {}),       # Steam Linux Runtime
             (6, "pv-adverb", "pv-adverb", {"SteamAppId": "1628350"}),
             (7, "steamwebhelper", "steamwebhelper", {"SteamAppId": "105600"})]
    assert not gamewatch.classify(procs)["gaming"]

def test_gamescope_gamemode_and_config():
    assert gamewatch.classify([(1, "gamescope-wl", "gamescope -- %command%", {})])["why"] == "gamescope"
    assert not gamewatch.classify([(1, "gamescope-wl", "", {})], cfg={"use_gamescope": False})["gaming"]
    assert gamewatch.classify([], gamemode=True)["why"] == "gamemode"
    assert gamewatch.classify([(1, "minecraft-launcher", "", {})], cfg={"game_processes": ["Minecraft-Launcher"]})["gaming"]
    assert not gamewatch.classify([(1, "blender", "", {"SteamAppId": "365670"})], cfg={"never_hide_for": ["blender"]})["gaming"]

def test_gamemode_status_line():
    assert gamewatch.gamemode_active(lambda argv, timeout=3: (0, "gamemode is active"))
    assert not gamewatch.gamemode_active(lambda argv, timeout=3: (0, "gamemode is inactive"))
    assert not gamewatch.gamemode_active(lambda argv, timeout=3: (127, "gamemoded: not installed"))

def test_unreadable_proc_never_raises(tmp_path):
    assert gamewatch.detect({"use_gamemode": False}, None, str(tmp_path / "missing"))["gaming"] is False

def test_watcher_reports_changes_only():
    seq = iter([{"gaming": False}, {"gaming": True, "why": "steam"}, {"gaming": True}, {"gaming": False}])
    seen = []
    w = gamewatch.GameWatcher(lambda: next(seq), listeners=[lambda g, i: seen.append(g)])
    assert [w.poll() for _ in range(4)] == [False, True, False, True]
    assert seen == [True, False]

def test_auto_game_mode_only_undoes_its_own():
    calls, mode = [], {"m": "normal"}
    def set_mode(kind, on): calls.append((kind, on)); mode["m"] = kind if on else "normal"
    a = gamewatch.AutoGameMode(set_mode, lambda: mode["m"])
    a(True); a(False)
    assert calls == [("game", True), ("game", False)]
    calls.clear(); mode["m"] = "stream"                 # he chose stream mode himself: leave it alone
    a(True); a(False)
    assert calls == []

def test_wire_publishes_to_hud_and_sets_game_mode(tmp_path):
    ran = []
    bus = hud.HudBus(str(tmp_path / "hud.json"))
    w = gamewatch.wire({"gamewatch": {"use_gamemode": False}}, lambda argv, timeout=15: ran.append(argv) or (0, ""), hud=bus,
                       mode_state=str(tmp_path / "mode.json"))
    w.detect_fn = lambda: {"gaming": True, "why": "steam"}
    w.poll()
    assert json.load(open(tmp_path / "hud.json"))["game"] is True
    assert ["powerprofilesctl", "set", "performance"] in ran
    w.detect_fn = lambda: {"gaming": False}
    w.poll()
    assert json.load(open(tmp_path / "hud.json"))["game"] is False
    assert ["powerprofilesctl", "set", "balanced"] in ran

class Ring:
    def __init__(self): self.vis = False
    def set_state(self, s): pass
    def set_caption(self, *a): pass
    def set_heard(self, *a): pass
    def set_level(self, v): pass
    def isVisible(self): return self.vis
    def show(self): self.vis = True
    def hide(self): self.vis = False

def test_hud_hides_in_games_but_still_answers():
    t = {"now": 100.0}
    ring = Ring(); f = hud.Follower(ring, {"always_show_on_desktop": True}, clock=lambda: t["now"])
    assert f.tick({"state": "idle"}) is True                         # desktop: always shown (his setting)
    assert f.tick({"state": "idle", "game": True, "ts": 1}) is False   # in a game: hidden
    assert f.tick({"state": "speaking", "game": True, "caption": "Clipped", "caption_ts": 2}) is True
    t["now"] += 10
    assert f.tick({"state": "idle", "game": True}) is False          # and gone again afterwards

def test_real_proc_scan_sees_this_process():
    assert any(pid == os.getpid() for pid, *_ in gamewatch.scan_procs())
