"""BitLocker fallback for the Tab link: recovery-key unlock (read-only) and one-time link codes."""
import json, os, subprocess, types
from jarvisd import cli, linkcode, tablink
from jarvisd.core import replies

KEY = "q5bS7nYv0nO1q2r3s4t5u6v7w8x9y0z1A2B3C4D5E6F"
SETTINGS = {"forwarding": {"to": "pc@example.com", "relay_url": "https://relay.example/x"},
            "replies": {"enabled": True, "topic": "t-secret", "key": KEY}, "assistant": {"address_as": "sir"},
            "hotkeys": {"ignored": True}}
UNLOCK = os.path.join(os.path.dirname(__file__), "..", "..", "image", "system_files", "usr", "libexec", "jarvis-os", "windows-ro-unlock")

def test_recovery_key_normalized():
    k = "123456-234567-345678-456789-567890-678901-789012-890123"
    assert tablink.normalize_recovery_key(k) == k
    assert tablink.normalize_recovery_key(k.replace("-", " ")) == k
    assert tablink.normalize_recovery_key("12345") is None and tablink.normalize_recovery_key("") is None

def test_bitlocker_partitions_biggest_first():
    out = json.dumps({"blockdevices": [{"path": "/dev/nvme0n1", "fstype": None, "size": 2, "children": [
        {"path": "/dev/nvme0n1p3", "fstype": "BitLocker", "size": 900}, {"path": "/dev/nvme0n1p4", "fstype": "ntfs", "size": 1},
        {"path": "/dev/nvme0n1p5", "fstype": "BitLocker", "size": 9000}]}]})
    run = lambda *a, **k: types.SimpleNamespace(stdout=out, returncode=0)
    assert tablink.bitlocker_partitions(run) == ["/dev/nvme0n1p5", "/dev/nvme0n1p3"]

def test_unlock_passes_key_on_stdin_only():
    seen = {}
    def run(argv, input=None, **k):
        seen["argv"], seen["input"] = argv, input
        return types.SimpleNamespace(returncode=0, stdout="/run/jarvis/windows-ro\n", stderr="")
    assert tablink.unlock_windows_ro("/dev/x", "111111-" * 7 + "111111", run) == "/run/jarvis/windows-ro"
    assert seen["argv"][:3] == ["pkexec", tablink.UNLOCK, "open"] and "111111" not in " ".join(seen["argv"])
    assert seen["input"].startswith("111111")

def test_unlock_helper_is_read_only_and_parses():
    s = open(UNLOCK).read()
    assert "--readonly" in s and "mount -o ro," in s and "--key-file=-" in s
    assert subprocess.run(["bash", "-n", UNLOCK]).returncode == 0

def test_link_code_roundtrip():
    c = linkcode.new_code()
    assert linkcode.normalize(c.lower().replace("-", " ")) == c.replace("-", "")
    assert linkcode.normalize("ABCD-EFGH-IJK") is None
    box = {}
    linkcode.publish(c, SETTINGS, post=lambda srv, topic, tok: box.setdefault(topic, []).append(tok), now=1000)
    topic, key = linkcode.derive(c)
    assert list(box) == [topic] and "t-secret" not in box[topic][0] and topic.startswith("jarvis-link-")
    get = lambda srv, t, since: [{"message": m} for m in box.get(t, [])] + [{"message": "junk"}]
    got = linkcode.fetch(c, get=get, now=1000)
    assert got["replies"]["key"] == KEY and "hotkeys" not in got
    assert linkcode.fetch(c, get=get, now=1000 + linkcode.TTL_S + 1) is None          # expired
    other = linkcode.new_code()
    assert linkcode.fetch(other, get=lambda s, t, x: [{"message": m} for m in box[topic]], now=1000) is None

def test_cli_link_with_code(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(linkcode, "fetch", lambda code: dict(SETTINGS) if code == "7KQ2-M9XD-R4TB" else None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    cfg = tmp_path / "c.json"
    assert cli.main(["link", "--code", "7KQ2-M9XD-R4TB", "--config", str(cfg)]) == 0
    d = json.load(open(cfg))
    assert d["replies"]["key"] == KEY and d["forwarding"]["to"] == "pc@example.com" and "hotkeys" not in d
    assert cli.main(["link", "--code", "0000-0000-0000", "--config", str(cfg)]) == 1

def test_cli_bitlocker_unlocks_reads_and_closes(tmp_path, monkeypatch):
    win = tmp_path / "win"; (win / "Users" / "u" / "Desktop" / "JARVIS").mkdir(parents=True)
    (win / "Users" / "u" / "Desktop" / "JARVIS" / "config.json").write_text(json.dumps(SETTINGS))
    calls = []
    monkeypatch.setattr(tablink, "mount_windows_ro", lambda interactive=True: None)
    monkeypatch.setattr(tablink, "bitlocker_partitions", lambda: ["/dev/nvme0n1p3"])
    monkeypatch.setattr(tablink, "unlock_windows_ro", lambda dev, key: calls.append(("open", dev, key)) or str(win))
    monkeypatch.setattr(tablink, "lock_windows", lambda: calls.append(("close",)))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "c.json"
    a = cli.argparse.Namespace(code=None, from_windows=True, bitlocker=True, windows_root=None, config=str(cfg), first_boot=False)
    assert cli.link(a, ask_key=lambda prompt: "123456 " * 8) == 0
    assert calls[0][:2] == ("open", "/dev/nvme0n1p3") and calls[-1] == ("close",)
    assert json.load(open(cfg))["replies"]["key"] == KEY

def test_first_boot_bitlocker_message_names_both_ways(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(tablink, "mount_windows_ro", lambda interactive=True: None)
    monkeypatch.setattr(tablink, "bitlocker_present", lambda: True)
    sent = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: sent.append(argv) or types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    a = cli.argparse.Namespace(code=None, from_windows=True, bitlocker=False, windows_root=None, config=str(tmp_path / "c.json"), first_boot=True)
    assert cli.link(a) == 0
    out = capsys.readouterr().out
    assert "--bitlocker" in out and "--code" in out
    assert sent and sent[0][0] == "notify-send"
