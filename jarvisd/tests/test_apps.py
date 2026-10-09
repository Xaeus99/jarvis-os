import json, os
from jarvisd import apps, brain, cli

ACF = """"AppState"
{
	"appid"		"%s"
	"name"		"%s"
	"installdir"		"%s"
	"SizeOnDisk"		"1000"
	"UserConfig"
	{
		"name"		"nested ignored"
	}
}
"""

def make_windows(tmp):
    c = tmp / "win_c"; d = tmp / "win_d"
    (c / "Windows" / "System32").mkdir(parents=True)
    steamapps = c / "Program Files (x86)" / "Steam" / "steamapps"; steamapps.mkdir(parents=True)
    (steamapps / "libraryfolders.vdf").write_text('"libraryfolders"\n{\n "0"\n {\n  "path"  "C:\\\\Program Files (x86)\\\\Steam"\n }\n "1"\n {\n  "path"  "D:\\\\SteamLibrary"\n }\n}\n')
    (steamapps / "appmanifest_105600.acf").write_text(ACF % ("105600", "Terraria", "Terraria"))
    (steamapps / "appmanifest_359550.acf").write_text(ACF % ("359550", "Tom Clancy's Rainbow Six Siege", "Tom Clancy's Rainbow Six Siege"))
    (steamapps / "appmanifest_228980.acf").write_text(ACF % ("228980", "Steamworks Common Redistributables", "Steamworks Shared"))
    lib = d / "SteamLibrary" / "steamapps"; lib.mkdir(parents=True)
    (lib / "appmanifest_2767030.acf").write_text(ACF % ("2767030", "Marvel Rivals", "MarvelRivals"))
    return {"C": str(c), "E": str(d)}      # Linux guesses the wrong letter for the second drive on purpose

COMPAT = os.path.join(os.path.dirname(__file__), "..", "..", "image", "system_files", "usr", "share", "jarvis-os", "apps", "steam-compat.json")

def test_parse_acf_flat_values_only():
    kv = apps.parse_acf(ACF % ("1", "Game", "dir"))
    assert kv["appid"] == "1" and kv["name"] == "Game" and kv["sizeondisk"] == "1000"

def test_find_windows_steam_reads_all_libraries(tmp_path):
    games = apps.find_windows_steam(make_windows(tmp_path))
    assert sorted(g["appid"] for g in games) == ["105600", "228980", "2767030", "359550"]

def test_plan_restore_skips_windows_only_and_gives_tweaks(tmp_path):
    games = apps.find_windows_steam(make_windows(tmp_path))
    install, skip, tweaks = apps.plan_restore(games, json.load(open(COMPAT)), str(tmp_path / "linux_steamapps"))
    assert sorted(g["appid"] for g in install) == ["105600", "2767030"]
    assert [s["appid"] for s in skip] == ["359550"] and "BattlEye" in skip[0]["why"]
    assert tweaks == [{"appid": "2767030", "name": "Marvel Rivals", "launch_options": "SteamDeck=1 %command%",
                       "why": tweaks[0]["why"]}]

def test_queue_downloads_writes_stub_manifests_once(tmp_path):
    target = tmp_path / "steamapps"
    g = [{"appid": "105600", "name": "Terraria", "installdir": "Terraria"}]
    assert apps.queue_downloads(g, str(target)) == ["105600"]
    kv = apps.parse_acf((target / "appmanifest_105600.acf").read_text())
    assert kv["stateflags"] == "1026" and kv["installdir"] == "Terraria"
    assert apps.queue_downloads(g, str(target)) == []          # already queued -> untouched
    assert apps.plan_restore(g, {}, str(target))[0] == []        # Steam knows it now -> not queued again

def test_windows_drive_is_only_read(tmp_path):
    mounts = make_windows(tmp_path)
    before = {p: os.stat(p).st_mtime_ns for r in mounts.values() for p, _, _ in os.walk(r)}
    games = apps.find_windows_steam(mounts)
    apps.queue_downloads(apps.plan_restore(games, {}, str(tmp_path / "linux"))[0], str(tmp_path / "linux"))
    after = {p: os.stat(p).st_mtime_ns for r in mounts.values() for p, _, _ in os.walk(r)}
    assert before == after

def test_flatpak_list_parses_and_installs_missing_only(tmp_path):
    lst = os.path.join(os.path.dirname(COMPAT), "flatpaks.list")
    ids = apps.load_list(lst)
    assert "com.discordapp.Discord" in ids and "com.obsproject.Studio" in ids and all(" " not in i for i in ids)
    calls = []
    def run(argv, timeout=15, input_text=None):
        calls.append(argv)
        if argv[:2] == ["flatpak", "list"]:
            return 0, "com.discordapp.Discord\n"
        if argv[:2] == ["flatpak", "install"] and argv[-1] == "com.spotify.Client":
            return 1, "error"
        return 0, ""
    res = apps.install_flatpaks(run, ids)
    assert res["com.discordapp.Discord"] == "already" and res["com.spotify.Client"] == "failed"
    assert res["com.brave.Browser"] == "ok"
    assert all("--user" in c for c in calls if c[:2] == ["flatpak", "install"])

def test_games_cli_dry_run_lists_plan(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(apps, "SHARE", os.path.dirname(COMPAT))
    mounts = make_windows(tmp_path)
    rc = cli.main(["--dry-run", "games", "restore", "--mount", f"C={mounts['C']}", "--mount", f"E={mounts['E']}",
                   "--steamapps", str(tmp_path / "lin")])
    out = capsys.readouterr().out
    plan = json.loads(out[out.index("{"):])
    assert rc == 0 and not (tmp_path / "lin").exists()           # dry run writes nothing
    assert [g["appid"] for g in plan["stays_on_windows"]] == ["359550"]

def test_voice_phrases_route():
    assert brain.parse_os("restore my games")[0] == "games_restore"
    assert brain.parse_os("jarvis, download all my games from windows")[0] == "games_restore"
    assert brain.parse_os("install my apps")[0] == "apps_install"
    assert brain.parse_os("open my games folder") is None
