import json, os, stat, time
import pytest
from jarvisd import tablink, daemon
from jarvisd.core import replies

KEY = "q5bS7nYv0nO1q2r3s4t5u6v7w8x9y0z1A2B3C4D5E6F"

def test_seal_roundtrip_and_tamper():
    tok = replies.seal({"text": "Good evening, sir."}, KEY)
    assert replies.unseal(tok, KEY) == {"text": "Good evening, sir."}
    assert replies.unseal(tok[:-3] + "AAA", KEY) is None
    assert replies.unseal(tok, "x" + KEY[1:]) is None

def test_listener_accepts_sealed_rejects_junk_and_replays(tmp_path, monkeypatch):
    now = time.time()
    good = replies.seal({"text": "Done, sir.", "mid": "m1", "ts": now}, KEY)
    acts = replies.seal({"mid": "m2", "re": "jabcd", "ts": now - 900, "actions": [{"type": "ping"}]}, KEY)
    evs = [{"event": "message", "id": "e1", "time": now, "message": good},
           {"event": "message", "id": "e2", "time": now, "message": "hello from a stranger"},
           {"event": "message", "id": "e3", "time": now, "message": acts},
           {"event": "message", "id": "e4", "time": now, "message": good}]          # same mid again
    monkeypatch.setattr(replies, "fetch", lambda *a, **k: evs)
    L = replies.ReplyListener({"topic": "t", "key": KEY}, str(tmp_path / "r.json"), lambda t, m: None)
    out = L.poll_once()
    assert [m.get("mid") for m in out] == ["m1", "m2"]
    assert "replayed" in out[1]["rejected"]

def test_forwarder_falls_back_to_relay_and_remembers(tmp_path):
    sent = []
    def smtp(cfg, msg): sent.append(("smtp", msg["Subject"], msg.get_content())); return False, "blocked"
    def relay(cfg, fields): sent.append(("relay", fields["_subject"], fields["command"])); return True, "ok"
    f = tablink.Forwarder({"to": "tab@example.com", "smtp_hosts": ["mx.example.com"]}, str(tmp_path / "f.json"), smtp, relay)
    route, _ = f.send("what's the weather", cid="jtest1")
    assert route == "relay"
    assert sent[0][1] == "JARVIS CMD: what's the weather [jtest1]"
    assert "spoken to J.A.R.V.I.S. (JARVIS OS" in sent[0][2] and "Linux" in sent[0][2]
    assert json.load(open(tmp_path / "f.json"))["smtp_blocked"] is True
    sent.clear(); f.send("again", cid="jtest2")
    assert sent[0][0] == "relay"                      # port 25 known blocked: relay first

def test_forwarder_not_configured():
    assert not tablink.Forwarder({}).configured

def test_send_result_is_sealed_both_ways():
    posted, mails = [], []
    res = {"mid": "abc12345", "re": "jq1", "status": "ok", "results": [{"type": "ping", "ok": True}]}
    routes = tablink.send_result({"to": "tab@example.com"}, {"key": KEY, "topic": "t"}, res,
                                 post=lambda s, t, tok: posted.append((t, tok)),
                                 smtp=lambda cfg, msg: (mails.append(msg), (True, "smtp"))[1])
    assert routes == "topic+mail"
    assert posted[0][0] == replies.result_topic("t", KEY)
    assert replies.unseal(posted[0][1], KEY) == res
    assert mails[0]["Subject"] == "JARVIS RESULT [jq1] ok 1/1 (act abc12345)"

class FakeHands:
    def __init__(self): self.calls = []
    def run(self, argv, timeout=15, input_text=None): self.calls.append(argv); return (0, "")
    def open_uri(self, t): self.calls.append(["open", t]); return True
    def master(self, level=None, mute=None): self.calls.append(["master", level]); return (level or 40, False)
    def app_volume(self, app, level=None, mute=None): self.calls.append(["app", app, level]); return 1
    def screenshot(self): self.calls.append(["shot"]); return True

def test_action_runner_subset():
    said, h = [], FakeHands()
    r = tablink.ActionRunner(h, said.append, sleep=lambda s: None)
    out = r.run({"mid": "m", "re": "j1", "say": "On it, sir.", "actions": [
        {"type": "launch", "target": "spotify"}, {"type": "open_url", "url": "https://example.com"},
        {"type": "open_url", "url": "file:///etc/passwd"}, {"type": "media", "key": "next"},
        {"type": "volume", "level": 30}, {"type": "volume", "app": "discord", "level": 20},
        {"type": "powershell", "script": "Remove-Item C:\\"}, {"type": "screenshot"}]})
    ok = [x["ok"] for x in out["results"]]
    assert ok == [True, True, False, True, True, True, False, True]
    assert out["results"][6]["error"] == "not supported on JARVIS OS yet"
    assert out["status"] == "partial" and said == ["On it, sir."]
    assert ["gtk-launch", "spotify"] in h.calls and ["playerctl", "next"] in h.calls

def test_action_runner_refuses_rejected_and_disabled():
    r = tablink.ActionRunner(FakeHands(), lambda t: None)
    assert r.run({"rejected": "expired", "actions": [{"type": "ping"}]})["status"] == "rejected"
    r2 = tablink.ActionRunner(FakeHands(), lambda t: None, allowed=lambda: False)
    assert r2.run({"actions": [{"type": "ping"}]})["status"] == "refused"

def test_import_link_from_windows_tree(tmp_path):
    win = tmp_path / "win"; d = win / "Users" / "Player" / "Downloads" / "JARVIS_v5.1" / "JARVIS"; d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({
        "forwarding": {"to": "pc@example.com", "smtp_hosts": ["mx.example.com"], "relay_url": "https://relay.example/x"},
        "replies": {"enabled": True, "server": "https://ntfy.example", "topic": "secret-topic", "key": KEY},
        "assistant": {"address_as": "sir", "piper_voice": "en_GB-alan-medium", "piper_speed": 1.05},
        "apps": {"something": "windows-only"}}), encoding="utf-8")
    (win / "Users" / "Player" / "Documents").mkdir()
    (win / "Users" / "Player" / "Documents" / "config.json").write_text("{}")      # not a JARVIS config
    found = tablink.find_windows_configs(str(win))
    assert found == [str(d / "config.json")]
    lin = tmp_path / "home" / ".config" / "jarvis" / "config.json"; lin.parent.mkdir(parents=True)
    lin.write_text(json.dumps({"theme": "vibranium"}))
    cfg = tablink.import_link(found[0], str(lin))
    assert cfg["theme"] == "vibranium" and cfg["replies"]["topic"] == "secret-topic" and cfg["address"] == "sir"
    assert "apps" not in cfg
    assert stat.S_IMODE(os.stat(lin).st_mode) == 0o600

def test_wire_tab_end_to_end(tmp_path):
    from jarvisd.brain import LinuxBrain
    said, results = [], []
    b = LinuxBrain({}, state_dir=str(tmp_path), run=lambda argv, timeout=15, input_text=None: (0, ""), say=said.append)
    class L:
        def __init__(self, cfg, path, on_reply): self.on_reply = on_reply; self.boosted = 0
        def boost(self): self.boosted += 1
    cfg = {"forwarding": {"to": "pc@example.com", "relay_url": "https://relay.example/x"},
           "replies": {"topic": "t", "key": KEY}}
    fwd, lis = daemon.wire_tab(cfg, b, said.append, str(tmp_path), listener_cls=L,
                               send_result=lambda f, r, res: results.append(res) or "topic")
    sent = []
    fwd._smtp = lambda c, m: (False, "blocked"); fwd._relay = lambda c, f: (sent.append(f["command"]), (True, "ok"))[1]
    b.handle("write me a poem about the moon")
    assert sent == ["write me a poem about the moon"] and lis.boosted == 1
    lis.on_reply("Here it is, sir.", {"mid": "m", "actions": [{"type": "ping"}]})
    assert "Here it is, sir." in said and results[0]["status"] == "ok"
    lis.on_reply("old", {"stale": True})
    assert "old" not in said

def test_wire_tab_unlinked(tmp_path):
    from jarvisd.brain import LinuxBrain
    b = LinuxBrain({}, state_dir=str(tmp_path), run=lambda argv, timeout=15, input_text=None: (0, ""))
    assert daemon.wire_tab({}, b, lambda t: None, str(tmp_path)) == (None, None)
    assert b.forward is None


def test_first_login_mount_never_prompts():
    calls = []
    class R:
        def __init__(self, out): self.stdout = out
    def run(argv, **k):
        calls.append(argv)
        if argv[0] == "lsblk":
            return R(json.dumps({"blockdevices": [{"path": "/dev/x", "fstype": "disk", "size": 1, "children": [
                {"path": "/dev/x3", "fstype": "ntfs", "size": 100, "mountpoint": None}]}]}))
        return R("Error: Not authorized")
    assert tablink.mount_windows_ro(run=run, interactive=False) is None
    assert "--no-user-interaction" in calls[-1]
    tablink.mount_windows_ro(run=run)
    assert "--no-user-interaction" not in calls[-1]

def test_bitlocker_detected():
    class R:
        stdout = "vfat\n\nBitLocker\nntfs\n"
    assert tablink.bitlocker_present(run=lambda *a, **k: R())
