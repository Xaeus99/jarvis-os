import json, os, threading
from jarvisd.remote_control import RemoteControl, classify, SAFE, ASK, BLOCK

def rc(tmp_path, **kw):
    return RemoteControl(str(tmp_path / "remote.json"), **kw)

def test_classify_safe_and_risky():
    assert classify({"type": "open_app", "name": "steam"})[0] == SAFE
    assert classify({"type": "open_url", "url": "https://www.youtube.com"})[0] == SAFE
    assert classify({"type": "open_url", "url": "https://store.steampowered.com/checkout/?purchasetype=self"})[0] == ASK
    assert classify({"type": "purchase"}) == (ASK, "purchase")
    assert classify({"type": "send_message"}) == (ASK, "messaging")
    assert classify({"type": "delete", "path": "~/x"})[0] == ASK
    assert classify({"type": "keys", "keys": "enter"}, {"window": "Discord | #general"})[0] == ASK
    assert classify({"type": "keys", "keys": "enter"}, {"window": "Terraria"})[0] == SAFE
    assert classify({"type": "click", "label": "Place order"}, {"url": "https://amazon.com/checkout"})[0] == ASK
    assert classify({"type": "shell", "cmd": "ls"})[0] == BLOCK
    assert classify({"type": "disable_kill_switch"})[0] == BLOCK
    assert classify({"type": "mystery"})[0] == BLOCK

def test_nothing_runs_unless_driving(tmp_path):
    r = rc(tmp_path); ran = []
    assert r.submit({"type": "open_app"}, execute=ran.append)[0] == "refused" and ran == []

def test_grant_run_takeback(tmp_path):
    changes = []; stopped = []
    r = rc(tmp_path, on_change=changes.append, stop_injection=lambda: stopped.append(1))
    r.grant(10); ran = []
    assert r.submit({"type": "open_app", "name": "steam"}, execute=ran.append)[0] == "ran"
    r.queue.extend([{"type": "open_url"}] * 3)
    assert r.takeback() == 3
    assert not r.driving and stopped == [1] and changes[-1]["state"] == "off"
    assert json.load(open(tmp_path / "remote.json"))["state"] == "off"
    assert r.submit({"type": "open_app"}, execute=ran.append)[0] == "refused" and len(ran) == 1

def test_risky_action_asks_and_decline_blocks(tmp_path):
    r = rc(tmp_path); r.grant(); ran = []; asked = []
    st = r.submit({"type": "purchase", "item": "skin"}, ask=lambda q: asked.append(q) or False, execute=ran.append)
    assert st[0] == "declined" and ran == [] and "purchase" in asked[0]
    st = r.submit({"type": "purchase"}, ask=lambda q: True, execute=ran.append)
    assert st[0] == "ran" and len(ran) == 1

def test_kill_switch_while_asking_wins(tmp_path):
    r = rc(tmp_path); r.grant(); ran = []
    def ask(q):
        r.takeback(by="hotkey")      # the owner hits Meta+Shift+Esc while the question is up, then says yes
        return True
    assert r.submit({"type": "send_message"}, ask=ask, execute=ran.append)[0] == "refused" and ran == []

def test_session_expires(tmp_path):
    now = [1000.0]
    r = rc(tmp_path, clock=lambda: now[0]); r.grant(minutes=1)
    assert r.driving
    now[0] += 61
    assert not r.driving

def test_second_process_sees_takeback(tmp_path):
    a = rc(tmp_path); a.grant()
    b = rc(tmp_path); assert b.driving          # e.g. the indicator process
    a.takeback(); assert not b.driving
