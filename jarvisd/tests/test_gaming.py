import json, os
from jarvisd import gaming, brain

KS = {"outputs": [
    {"name": "DP-1", "connected": True, "enabled": True, "currentModeId": "3", "vrrPolicy": 0,
     "modes": [{"id": "1", "name": "2560x1440@180", "refreshRate": 179.95, "size": {"width": 2560, "height": 1440}},
               {"id": "2", "name": "2560x1440@144", "refreshRate": 143.9, "size": {"width": 2560, "height": 1440}},
               {"id": "3", "name": "2560x1440@60", "refreshRate": 59.95, "size": {"width": 2560, "height": 1440}},
               {"id": "4", "name": "1920x1080@240", "refreshRate": 240.0, "size": {"width": 1920, "height": 1080}}]},
    {"name": "HDMI-A-1", "connected": False, "modes": []}]}

class Rec:
    def __init__(self, answers=None): self.calls = []; self.answers = answers or {}
    def __call__(self, argv, timeout=15, input_text=None):
        self.calls.append(argv)
        for k, v in self.answers.items():
            if tuple(argv[:len(k)]) == k:
                return v
        return 0, ""

def test_display_picks_native_highest_refresh_and_vrr():
    cmds = gaming.best_display_commands(json.dumps(KS))
    assert cmds == ["output.DP-1.mode.1", "output.DP-1.vrrpolicy.automatic"]     # 1440p@180, not 1080p@240

def test_preferred_mode_beats_bigger_virtual_modes():
    ks = json.loads(json.dumps(KS))
    ks["outputs"][0]["preferredModes"] = ["2"]
    ks["outputs"][0]["modes"].append({"id": "9", "name": "5120x2160@60", "refreshRate": 60.0, "size": {"width": 5120, "height": 2160}})
    assert gaming.best_display_commands(ks)[0] == "output.DP-1.mode.1"     # 1440p (preferred size) at its top refresh

def test_hdr_is_opt_in():
    assert not any("hdr" in c for c in gaming.best_display_commands(KS))
    assert "output.DP-1.hdr.enable" in gaming.best_display_commands(KS, hdr=True)
    assert "output.DP-1.hdr.disable" in gaming.best_display_commands(KS, hdr=False)

def test_game_mode_on_off_is_reversible(tmp_path):
    st = str(tmp_path / "mode.json")
    r = Rec()
    gaming.set_mode(r, "game", True, {}, st)
    assert r.calls == [["powerprofilesctl", "set", "performance"]]
    r2 = Rec()
    gaming.set_mode(r2, "game", False, {}, st)
    assert ["powerprofilesctl", "set", "balanced"] in r2.calls
    assert ["pw-metadata", "-n", "settings", "0", "clock.force-quantum", "0"] in r2.calls
    assert json.load(open(st))["mode"] == "normal"

def test_stream_mode_lowers_latency_holds_notifications_and_releases_them(tmp_path):
    st = str(tmp_path / "mode.json")
    r = Rec()
    ok, failed = gaming.set_mode(r, "stream", True, {}, st)
    assert ["pw-metadata", "-n", "settings", "0", "clock.force-quantum", "256"] in r.calls
    assert ["balooctl6", "suspend"] in r.calls and not failed
    # Do Not Disturb is held by a long-running user unit (a one-shot D-Bus call is released as soon as it exits)
    assert any(c[:3] == ["systemd-run", "--user", "--unit=jarvis-dnd"] and "dnd-hold" in c for c in r.calls)
    r2 = Rec()
    gaming.set_mode(r2, "stream", False, {}, st)
    assert ["systemctl", "--user", "stop", "jarvis-dnd.service"] in r2.calls

def test_dnd_hold_command_exists():
    from jarvisd import cli
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["dnd-hold", "--help"])

def test_scheduler_only_when_opted_in(tmp_path):
    assert not any("scxctl" in " ".join(s) for s in gaming.mode_steps("game", True, {}))
    steps = gaming.mode_steps("game", True, {"game_scheduler": "lavd"})
    assert ["pkexec", "scxctl", "start", "--sched", "lavd", "--mode", "gaming"] in steps

SMI = """
    FB Memory Usage
        Total                             : 8151 MiB
        Reserved                          : 300 MiB
    BAR1 Memory Usage
        Total                             : 8192 MiB
        Used                              : 5 MiB
"""

def test_rebar_detection():
    assert gaming.parse_bar1(SMI) == (8192, 8151)
    r = Rec({("nvidia-smi", "-q"): (0, SMI), ("nvidia-smi", "--query-gpu"): (0, "NVIDIA GeForce RTX 5060, 610.43, 45\n"),
             ("powerprofilesctl",): (0, "performance\n"), ("kscreen-doctor",): (0, json.dumps(KS)),
             ("gamemoded",): (1, ""), ("scxctl",): (1, "")})
    d = dict(gaming.gaming_check(r))
    assert d["Resizable BAR"].startswith("on") and d["Power profile"] == "performance"
    assert d["Display DP-1"] == "2560x1440@60 · VRR never" and d["CPU scheduler"].startswith("kernel default")
    small = SMI.replace("8192 MiB", "256 MiB")
    r.answers[("nvidia-smi", "-q")] = (0, small)
    assert dict(gaming.gaming_check(r))["Resizable BAR"].startswith("off")

def test_voice_routes():
    P = brain.parse_os
    assert P("game mode") == ("mode", {"kind": "game", "on": True})
    assert P("game mode off") == ("mode", {"kind": "game", "on": False})
    assert P("start stream mode") == ("mode", {"kind": "stream", "on": True})
    assert P("end streaming mode") == ("mode", {"kind": "stream", "on": False})
    assert P("gaming check")[0] == "gaming_check" and P("is my pc ready for streaming")[0] == "gaming_check"
    assert P("set up my monitor") == ("display_setup", {"hdr": None})
    assert P("turn on hdr") == ("display_setup", {"hdr": True}) and P("hdr off") == ("display_setup", {"hdr": False})

def test_brain_game_mode_speaks(tmp_path, monkeypatch):
    monkeypatch.setattr(gaming, "STATE", str(tmp_path / "m.json"))
    r = Rec()
    b = brain.LinuxBrain(run=r, state_dir=str(tmp_path))
    assert "Game mode on" in b.handle("game mode")
    assert ["powerprofilesctl", "set", "performance"] in r.calls

def test_shipped_tuning_files():
    sf = os.path.join(os.path.dirname(__file__), "..", "..", "image", "system_files")
    env = open(os.path.join(sf, "usr/lib/environment.d/60-jarvis-gaming.conf")).read()
    assert "PROTON_ENABLE_NVAPI=1" in env
    mh = open(os.path.join(sf, "etc/skel/.config/MangoHud/MangoHud.conf")).read()
    assert "no_display" in mh and "toggle_hud=Shift_R+F12" in mh
    obs = json.load(open(os.path.join(sf, "usr/share/jarvis-os/obs/JARVIS_Stream/streamEncoder.json")))
    assert obs["rate_control"] == "CBR" and obs["bitrate"] <= 6000
    ini = open(os.path.join(sf, "usr/share/jarvis-os/obs/JARVIS_Stream/basic.ini")).read()
    assert "Encoder=obs_nvenc_h264_tex" in ini and "RecEncoder=obs_nvenc_av1_tex" in ini
