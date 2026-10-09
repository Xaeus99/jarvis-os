import pytest
from jarvisd.brain import parse_os
from conftest import Rec, EFI

@pytest.mark.parametrize("phrase,action", [
    ("switch to windows", "switch_windows"), ("reboot into windows", "switch_windows"), ("Jarvis, boot windows", "switch_windows"),
    ("take back control", "remote_takeback"), ("stop tab", "remote_takeback"), ("hands off tab", "remote_takeback"),
    ("let tab drive", "remote_grant"), ("tab take over for 10 minutes", "remote_grant"),
    ("check for updates", "update_check"), ("update jarvis os", "update_stage"), ("roll back the update", "rollback"),
    ("set the lights to purple", "rgb"), ("lights off", "rgb"), ("rgb gaming", "rgb"),
    ("how hot is my gpu", "gpu_stats"), ("restart to bios", "reboot_bios"),
])
def test_parse_os(phrase, action):
    assert parse_os(phrase)[0] == action

@pytest.mark.parametrize("phrase", ["open windows explorer", "what is windows", "set a timer for 5 minutes", "open steam"])
def test_parse_os_leaves_other_phrases(phrase):
    assert parse_os(phrase) is None

def test_switch_to_windows_asks_and_decline_runs_nothing(rec, brain_factory):
    b = brain_factory(rec, answer="no")
    assert "Staying" in b.handle("switch to windows")
    assert not rec.ran("pkexec") and not rec.ran("systemctl")

def test_switch_to_windows_yes(rec, brain_factory):
    b = brain_factory(rec, answer="yes")
    b.handle("switch to windows")
    assert rec.ran("pkexec", "/usr/libexec/jarvis-os/bootnext-windows") and rec.ran("systemctl", "reboot")

def test_switch_no_windows_entry(brain_factory):
    r = Rec({("efibootmgr",): (0, "BootCurrent: 0001\nBoot0001* JARVIS OS\tHD()\n")})
    b = brain_factory(r, answer="yes")
    assert "can't find Windows" in b.handle("switch to windows") and not r.ran("pkexec")

def test_takeback_by_voice(rec, brain_factory):
    b = brain_factory(rec, answer="yes")
    b.handle("let tab drive for 15 minutes"); assert b.remote.driving
    out = b.handle("take back control")
    assert not b.remote.driving and "You have the controls" in out and rec.ran("systemctl", "--user", "stop", "ydotoold.service")

def test_grant_needs_yes(rec, brain_factory):
    b = brain_factory(rec, answer="")
    b.handle("let tab drive"); assert not b.remote.driving

def test_rgb(rec, brain_factory):
    b = brain_factory(rec)
    b.handle("set the lights to purple"); assert rec.calls[-1] == ["openrgb", "--noautoconnect", "--mode", "static", "--color", "8000FF"]
    b.handle("lights off"); assert rec.calls[-1][-1] == "000000"
    b.handle("rgb gaming"); assert rec.calls[-1] == ["openrgb", "--noautoconnect", "--profile", "Gaming"]

def test_r6_points_to_windows(rec, brain_factory):
    b = brain_factory(rec)
    assert "only runs on Windows" in b.handle("open rainbow six siege") and rec.calls == []

def test_games_launch_through_steam(rec, brain_factory):
    b = brain_factory(rec)
    b.handle("open marvel rivals"); assert rec.calls[-1] == ["steam", "steam://rungameid/2767030"]
    b.handle("open terraria"); assert rec.calls[-1] == ["steam", "steam://rungameid/105600"]

def test_volume_and_media(rec, brain_factory):
    b = brain_factory(rec)
    assert "40 percent" in b.handle("set volume to 40")
    assert ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "40%"] in rec.calls
    b.handle("next song"); assert rec.calls[-1] == ["playerctl", "next"]

def test_shared_v51_local_commands(rec, brain_factory):
    b = brain_factory(rec)
    assert "Timer set for 5 minutes" in b.handle("set a timer for 5 minutes")
    assert "108" in b.handle("what is 12 times 9")

def test_updates(brain_factory):
    r = Rec({("bootc", "upgrade", "--check"): (0, "Update available for ghcr.io/x/jarvis-os:stable\n  Version: 0.1.20261010\n")})
    b = brain_factory(r)
    assert "update is ready" in b.handle("check for updates")
    assert "staged" in b.handle("update jarvis os") and r.ran("bootc", "upgrade")
    assert not r.ran("systemctl", "reboot")                  # staging never reboots

def test_rollback_needs_yes(rec, brain_factory):
    b = brain_factory(rec, answer="no"); b.handle("roll back the update"); assert not rec.ran("bootc", "rollback")

def test_gpu(brain_factory):
    r = Rec({("nvidia-smi",): (0, "61, 37, 2311, 8151\n")})
    out = brain_factory(r).handle("how hot is my gpu")
    assert "61 degrees" in out and "37 percent" in out and "of 8 gigabytes" in out

def test_unknown_goes_to_tab_only_when_linked(rec, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from jarvisd.brain import LinuxBrain
    sent = []
    b = LinuxBrain({}, state_dir=str(tmp_path / "s"), run=rec, forward=sent.append)
    assert "Passing that to Tab" in b.handle("what's the best build for my character in terraria")
    assert sent and rec.calls == []
