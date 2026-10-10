import json, os
import pytest
from jarvisd import studio
from jarvisd.core import obs as obsmod

class FakeOBS:
    """Speaks the same request() surface as core/obs.OBS."""
    def __init__(self, scenes=("Starting Soon", "Gameplay", "Just Chatting", "BRB"), replay=True, wrong_pw=False):
        self.calls = []; self.streaming = False; self.recording = False; self.replay = replay
        self.scenes = list(scenes); self.current = scenes[0]; self.wrong_pw = wrong_pw
    def request(self, t, data=None):
        if self.wrong_pw:
            raise obsmod.OBSError("OBS refused the websocket password")
        self.calls.append((t, data))
        if t == "GetStreamStatus": return {"outputActive": self.streaming}
        if t == "GetRecordStatus": return {"outputActive": self.recording}
        if t == "GetReplayBufferStatus": return {"outputActive": self.replay}
        if t == "StartStream": self.streaming = True
        if t == "StopStream": self.streaming = False
        if t == "StartRecord": self.recording = True
        if t == "StopRecord": self.recording = False
        if t == "StartReplayBuffer": self.replay = True
        if t == "GetLastReplayBufferReplay": return {"savedReplayPath": "/home/u/Videos/Replay 2026-10-10.mkv"}
        if t == "GetSceneList": return {"scenes": [{"sceneName": s} for s in self.scenes]}
        if t == "SetCurrentProgramScene": self.current = data["sceneName"]
        return {}
    ok = obsmod.OBS.ok
    stream_active = obsmod.OBS.stream_active; record_active = obsmod.OBS.record_active
    replay_active = obsmod.OBS.replay_active; ensure_replay_buffer = obsmod.OBS.ensure_replay_buffer
    save_replay = obsmod.OBS.save_replay
    def close(self): pass

def make(tmp_path, fake=None, running=True, cfg=None, launched=None, modes=None):
    fake = fake or FakeOBS()
    seen = {}
    def factory(st):
        seen["st"] = st
        return fake
    st = studio.Studio(cfg or {}, launch=lambda argv: (launched.append(argv) if launched is not None else None) or (0, ""),
                       run=lambda argv, timeout=15: (1, ""), set_mode=(lambda k, on: modes.append((k, on))) if modes is not None else None,
                       sleep=lambda s: None, client_factory=factory,
                       procs=lambda: [("obs", "/app/bin/obs")] if running else [("plasmashell", "plasmashell")], home=str(tmp_path))
    return st, fake, seen

def write_ws(tmp_path, **kw):
    d = tmp_path / ".var/app/com.obsproject.Studio/config/obs-studio/plugin_config/obs-websocket"
    d.mkdir(parents=True); (d / "config.json").write_text(json.dumps(kw)); return d / "config.json"

def test_password_comes_from_obs_own_settings(tmp_path):
    write_ws(tmp_path, server_enabled=True, server_port=4460, auth_required=True, server_password="from-obs")
    st = studio.websocket_settings({}, str(tmp_path))
    assert (st["port"], st["password"], st["enabled"], st["source"]) == (4460, "from-obs", True, "OBS settings")
    st = studio.websocket_settings({"stream": {"obs_websocket": {"password": "mine"}}}, str(tmp_path))
    assert st["password"] == "mine" and st["source"] == "jarvis config"
    assert studio.websocket_settings({}, str(tmp_path / "nobody"))["password"] == ""

def test_no_password_is_hard_coded():
    src = open(studio.__file__).read()
    assert "server_password\"] = secrets.token_urlsafe" in src
    import re
    assert re.findall(r"password[\"']?\]?\s*[:=]\s*[\"'][^\"']+[\"']", src) == []        # no literal password anywhere

def test_enable_websocket_keeps_existing_password(tmp_path):
    p = write_ws(tmp_path, server_enabled=False, server_password="keep", server_port=4455)
    studio.enable_websocket(str(tmp_path))
    w = json.loads(p.read_text())
    assert w["server_enabled"] and w["auth_required"] and w["server_password"] == "keep"
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"

def test_enable_websocket_makes_a_random_password(tmp_path):
    p1 = studio.enable_websocket(str(tmp_path / "a")); p2 = studio.enable_websocket(str(tmp_path / "b"))
    a, b = json.load(open(p1))["server_password"], json.load(open(p2))["server_password"]
    assert len(a) >= 20 and a != b

def test_go_live_opens_obs_and_discord_and_streams(tmp_path):
    launched, modes = [], []
    st, fake, _ = make(tmp_path, running=False, launched=launched, modes=modes)
    msg = st.start("stream")
    assert launched == [["flatpak", "run", "com.obsproject.Studio"], ["flatpak", "run", "com.discordapp.Discord"]]
    assert "You're live" in msg and fake.streaming and ("stream", True) in modes
    # OBS's websocket was off in its settings: switched on before OBS started, with its own password
    w = json.load(open(studio.websocket_file(str(tmp_path))))
    assert w["server_enabled"] and w["server_password"]
    assert "You're already live" in st.start("stream")
    assert "Stream ended" in st.stop("stream") and not fake.streaming and ("stream", False) in modes
    assert "not live" in st.stop("stream")

def test_record_start_stop(tmp_path):
    st, fake, _ = make(tmp_path)
    assert st.start("record") == "Recording, sir." and fake.recording
    assert "Already recording" in st.start("record")
    assert "stopped and saved" in st.stop("record") and not fake.recording

def test_clip_that(tmp_path):
    st, fake, _ = make(tmp_path)
    assert st.clip().startswith("Clipped, sir.") and ("SaveReplayBuffer", None) in fake.calls
    st, fake, _ = make(tmp_path, FakeOBS(replay=False))
    assert "on now" in st.clip() and fake.replay
    st, fake, _ = make(tmp_path, running=False)
    assert "Linux has no Game Bar" in st.clip()

def test_wrong_password_is_reported_not_retried(tmp_path):
    st, fake, _ = make(tmp_path, FakeOBS(wrong_pw=True))
    msg = st.start("stream")
    assert "rejected the websocket password" in msg and "press Start Streaming yourself" in msg

def test_switch_scene_fuzzy(tmp_path):
    st, fake, _ = make(tmp_path)
    assert st.scene("gameplay") == "Switched to Gameplay, sir." and fake.current == "Gameplay"
    assert st.scene("chatting") == "Switched to Just Chatting, sir."
    assert st.scene("be right back") .startswith("I don't see") or fake.current == "BRB"
    assert "I don't see a scene called pizza" in st.scene("pizza")

def test_match_scene():
    sc = ["Scene", "Game Capture", "Starting Soon"]
    assert studio.match_scene("game", sc) == "Game Capture"
    assert studio.match_scene("starting soon scene", sc) == "Starting Soon"
    assert studio.match_scene("xyz", sc) is None

def test_obs_running_detection():
    assert studio.obs_running([("obs", "/app/bin/obs")])
    assert studio.obs_running([("bwrap", "/usr/bin/flatpak run --branch=stable com.obsproject.Studio")])
    assert not studio.obs_running([("plasmashell", "plasmashell"), ("dolphin", "dolphin /home/u/com.obsproject.Studio")])

def test_obs_auth_handshake(monkeypatch):
    """core/obs.py against a scripted obs-websocket v5 server (hello with auth -> identify -> identified)."""
    import websocket
    sent = []
    class WS:
        def __init__(self): self.q = [json.dumps({"op": 0, "d": {"rpcVersion": 1, "authentication": {"salt": "s", "challenge": "c"}}}),
                                      json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}})]
        def recv(self): return self.q.pop(0)
        def send(self, m): sent.append(json.loads(m))
        def close(self): pass
    monkeypatch.setattr(websocket, "create_connection", lambda url, timeout=4: WS())
    obsmod.OBS(password="pw").connect()
    assert sent[0]["op"] == 1 and sent[0]["d"]["authentication"] == obsmod.auth_string("pw", "s", "c")
    with pytest.raises(obsmod.OBSError):
        obsmod.OBS(password="").connect()

def test_voice_routes(brain_factory, rec):
    b = brain_factory(rec)
    class S:
        def __init__(self): self.calls = []
        def start(self, mode, s): self.calls.append(("start", mode)); return "ok"
        def stop(self, mode, s): self.calls.append(("stop", mode)); return "ok"
        def clip(self, s): self.calls.append(("clip",)); return "ok"
        def scene(self, name, s): self.calls.append(("scene", name)); return "ok"
    b.studio = S()
    for t in ["go live", "stop the stream", "start recording", "stop recording", "clip that",
              "switch to the gameplay scene", "switch scene to just chatting"]:
        b.handle(t)
    assert b.studio.calls == [("start", "stream"), ("stop", "stream"), ("start", "record"), ("stop", "record"), ("clip",),
                              ("scene", "gameplay"), ("scene", "just chatting")]
    assert "switch_windows" == __import__("jarvisd.brain", fromlist=["x"]).parse_os("switch to windows")[0]
