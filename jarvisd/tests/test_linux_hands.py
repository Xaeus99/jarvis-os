import pytest
from jarvisd.linux_hands import LinuxHands, ydotool_args
from conftest import Rec

def test_ydotool_args():
    assert ydotool_args("ctrl+shift+t") == ["29:1", "42:1", "20:1", "20:0", "42:0", "29:0"]
    with pytest.raises(ValueError):
        ydotool_args("ctrl+ünknown")

def test_master_volume_parse():
    r = Rec({("wpctl", "get-volume"): (0, "Volume: 0.55 [MUTED]\n")})
    assert LinuxHands(run=r).master(mute=True) == (55, True)

def test_app_volume_matches_sink_inputs():
    pactl = ('Sink Input #41\n\tProperties:\n\t\tapplication.name = "Discord"\n\t\tapplication.process.binary = "Discord"\n'
             'Sink Input #52\n\tProperties:\n\t\tapplication.name = "Firefox"\n')
    r = Rec({("pactl", "list", "sink-inputs"): (0, pactl)})
    assert LinuxHands(run=r).app_volume("discord", level=30) == 1
    assert ["pactl", "set-sink-input-volume", "41", "30%"] in r.calls and not any("52" in c for c in r.calls)

def test_open_uri_maps_windows_settings_to_kde():
    r = Rec(); h = LinuxHands(run=r)
    assert h.open_uri("ms-settings:sound")
    assert r.calls[-1] == ["systemsettings", "kcm_pulseaudio"]
    assert h.open_uri("https://www.youtube.com") and r.calls[-1] == ["xdg-open", "https://www.youtube.com"]

def test_window_ops_use_kwin_shortcuts():
    r = Rec(); LinuxHands(run=r).window("snap_left")
    assert r.calls[-1][-1] == "Window Quick Tile Left"

def test_clipboard_types():
    r = Rec({("wl-paste", "--list-types"): (0, "text/uri-list\ntext/plain\n")})
    assert LinuxHands(run=r).clip_get() == "\x00files"

def test_missing_tool_is_reported_not_crashing():
    from jarvisd.linux_hands import Runner
    code, out = Runner()(["definitely-not-a-real-binary-xyz"])
    assert code == 127 and "not installed" in out
