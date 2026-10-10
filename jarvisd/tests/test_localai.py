"""The offline brain (jarvisd/localai.py) against a fake Ollama on localhost, and the brain's routing."""
import io, json, os, subprocess, tarfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from jarvisd import localai, brain as B

class FakeOllama(BaseHTTPRequestHandler):
    models = set(); chats = []; reply = "Certainly, sir. **Paris** is the capital of France.\n- extra line"
    def log_message(self, *a): pass
    def _json(self, d, code=200):
        b = json.dumps(d).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/api/version": return self._json({"version": "0.40.2"})
        if self.path == "/api/tags": return self._json({"models": [{"name": m} for m in sorted(self.models)]})
        self._json({}, 404)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/pull":
            self.send_response(200); self.end_headers()
            for line in ({"status": "pulling manifest"}, {"status": "pulling x", "total": 100, "completed": 50},
                         {"status": "pulling x", "total": 100, "completed": 100}, {"status": "success"}):
                self.wfile.write((json.dumps(line) + "\n").encode())
            FakeOllama.models.add(body["model"]); return
        if self.path == "/api/chat":
            FakeOllama.chats.append(body)
            return self._json({"message": {"role": "assistant", "content": FakeOllama.reply}, "done": True})
        self._json({}, 404)

@pytest.fixture
def ollama():
    FakeOllama.models = set(); FakeOllama.chats = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()

def cfg(url, **kw):
    return {"assistant": dict({"local_ai_url": url}, **kw)}

def test_choose_model_for_the_hardware():
    assert localai.choose_model({}, nvidia=True, ram=8) == "llama3.2:3b"       # RTX card
    assert localai.choose_model({}, nvidia=False, ram=16) == "llama3.2:3b"
    assert localai.choose_model({}, nvidia=False, ram=4) == "llama3.2:1b"      # a small virtual PC
    assert localai.choose_model({"assistant": {"local_ai_model": "qwen2.5:3b"}}, nvidia=False, ram=4) == "qwen2.5:3b"

def test_speakable_strips_markdown_and_keeps_it_short():
    t = localai.speakable("**Sure**, sir.\n1. First thing.\n2. Second. Third. Fourth. See https://x.y")
    assert "*" not in t and "1." not in t and "http" not in t and t.count(".") <= 3

def test_ask_uses_persona_history_and_short_answers(ollama):
    ai = localai.LocalAI(cfg(ollama, local_ai_model="llama3.2:1b"))
    assert not ai.ready()                                     # server up, model not pulled yet
    assert localai.pull("llama3.2:1b", ollama) and ai.ready()
    a1 = ai.ask("what's the capital of France", sir="sir")
    assert a1 == "Certainly, sir. Paris is the capital of France. extra line"
    ai.ask("and of Spain?")
    sent = FakeOllama.chats[-1]
    assert sent["model"] == "llama3.2:1b" and sent["messages"][0]["role"] == "system"
    assert "J.A.R.V.I.S." in sent["messages"][0]["content"] and "teenager" in sent["messages"][0]["content"]
    assert [m["role"] for m in sent["messages"][1:]] == ["user", "assistant", "user"]
    ai.forget(); ai.ask("hi"); assert len(FakeOllama.chats[-1]["messages"]) == 2

def test_ask_returns_none_when_off_or_down():
    assert localai.LocalAI({"assistant": {"local_ai": False}}).ask("hi") is None
    assert localai.LocalAI(cfg("http://127.0.0.1:9")).ask("hi") is None

def _fake_runtime_archive(tmp_path):
    src = tmp_path / "src" / "bin"; src.mkdir(parents=True)
    (src / "ollama").write_text("#!/bin/sh\necho fake\n"); os.chmod(src / "ollama", 0o755)
    arc = tmp_path / "ollama-linux-amd64.tar.gz"
    with tarfile.open(arc, "w:gz") as t:
        t.add(src, arcname="bin")
    return arc

def test_setup_downloads_verifies_starts_and_pulls(tmp_path, ollama, monkeypatch):
    arc = _fake_runtime_archive(tmp_path)
    import hashlib
    sums = f"{hashlib.sha256(arc.read_bytes()).hexdigest()}  ./ollama-linux-amd64.tar.gz\n"
    def opener(rq, timeout=None):
        u = rq.full_url if hasattr(rq, "full_url") else rq
        if u.endswith("sha256sum.txt"): return io.BytesIO(sums.encode())
        if u.endswith(".tar.gz"):
            r = io.BytesIO(arc.read_bytes()); r.status = 200; r.headers = {"Content-Length": str(arc.stat().st_size)}; return r
        import urllib.request; return urllib.request.urlopen(rq, timeout=timeout)
    monkeypatch.setattr(localai, "OLLAMA_URL", "https://example.invalid/ollama-linux-amd64.tar.gz")
    monkeypatch.setattr(localai, "OLLAMA_SUMS", "https://example.invalid/sha256sum.txt")
    calls, notes = [], []
    def run(argv, **kw):
        calls.append(argv)
        return subprocess.run(argv, **kw) if argv[0] == "tar" else subprocess.CompletedProcess(argv, 0, "", "")
    home, state = str(tmp_path / "ollama"), str(tmp_path / "state.json")
    localai.install_runtime(home, url=localai.OLLAMA_URL, sums_url=localai.OLLAMA_SUMS, opener=opener, run=run)
    assert os.path.exists(os.path.join(home, "bin", "ollama")) and not os.path.exists(os.path.join(home, "ollama-linux-amd64.tar.gz"))
    st = localai.setup(cfg(ollama, local_ai_model="llama3.2:1b"), notify=lambda t, b, icon=None: notes.append(b),
                       run=run, home=home, state=state, wait=lambda s: None)
    assert st["status"] == "ready" and st["model"] == "llama3.2:1b"
    assert ["systemctl", "--user", "start", "jarvis-ollama.service"] in calls
    assert any("Step 2 of 2" in n for n in notes) and "without the internet" in notes[-1]
    assert json.load(open(state))["status"] == "ready"

def test_damaged_runtime_download_is_rejected(tmp_path):
    arc = _fake_runtime_archive(tmp_path)
    def opener(rq, timeout=None):
        u = rq.full_url
        if u.endswith("sha256sum.txt"): return io.BytesIO(b"0" * 64 + b"  ollama-linux-amd64.tar.gz\n")
        r = io.BytesIO(arc.read_bytes()); r.status = 200; r.headers = {"Content-Length": str(arc.stat().st_size)}; return r
    with pytest.raises(RuntimeError, match="damaged"):
        localai.install_runtime(str(tmp_path / "o"), url="https://x/ollama-linux-amd64.tar.gz", sums_url="https://x/sha256sum.txt",
                                opener=opener)

def test_setup_failure_is_reported_and_resumable(tmp_path):
    st = localai.setup(cfg("http://127.0.0.1:9", local_ai_model="m"), notify=lambda *a, **k: None,
                       run=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""), home=str(tmp_path / "o"),
                       state=str(tmp_path / "s.json"), wait=lambda s: None,
                       opener=lambda rq, timeout=None: (_ for _ in ()).throw(OSError("offline")))
    assert st["status"] == "failed" and "offline" in st["error"]
    assert "stopped" in localai.status_line(str(tmp_path / "s.json"))

def test_route():
    assert B.route("what's the capital of France", linked=False, ai_ready=True) == "ai"
    assert B.route("tell me a joke", linked=True, ai_ready=True) == "ai"
    assert B.route("check my email", linked=True, ai_ready=True) == "tab"
    assert B.route("what's the latest news", linked=True, ai_ready=True) == "tab"
    assert B.route("tell me a joke", linked=True, ai_ready=False) == "tab"
    assert B.route("check my email", linked=False, ai_ready=True) == "ai"      # no Tab: the offline brain says it can't
    assert B.route("tell me a joke", linked=False, ai_ready=False) is None

class FakeAI:
    def __init__(self, ready=True, answer="Paris, sir."): self._r, self.answer, self.asked, self.enabled = ready, answer, [], True
    def ready(self): return self._r
    def model(self): return "llama3.2:1b"
    def ask(self, t, sir="sir"): self.asked.append(t); return self.answer
    def forget(self): self.asked.append("<forget>")

def _brain(tmp_path, **kw):
    said = []
    b = B.LinuxBrain({}, state_dir=str(tmp_path), run=lambda argv, **k: (0, ""), say=lambda t: said.append(t) or t, **kw)
    return b, said

def test_brain_answers_offline_with_the_local_ai(tmp_path):
    b, said = _brain(tmp_path); b.ai = FakeAI()
    b.handle("what is the capital of France")
    assert said[-1] == "Paris, sir." and b.ai.asked == ["what is the capital of France"]
    b.handle("what time is it")                                 # built-in commands never reach the model
    assert len(b.ai.asked) == 1 and "It's" in said[-1]

def test_brain_without_any_brain_says_how_to_get_one(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "route", lambda *a: None)
    from jarvisd import localai as L
    monkeypatch.setattr(L, "read_state", lambda path=None: {})
    b, said = _brain(tmp_path); b.ai = FakeAI(ready=False)
    b.handle("what is the meaning of life")
    assert "download your offline brain" in said[-1]

def test_brain_sends_tasks_to_tab_when_linked(tmp_path):
    sent = []
    b, said = _brain(tmp_path, forward=sent.append); b.ai = FakeAI()
    b.handle("check my email for me")
    assert sent == ["check my email for me"] and said[-1].startswith("Passing that to Tab")
    b.handle("tell me a fun fact about space")
    assert len(sent) == 1 and said[-1] == "Paris, sir."

def test_voice_commands_for_the_offline_brain(tmp_path):
    for phrase, action in (("download your offline brain", "ai_setup"), ("jarvis, set up the local ai", "ai_setup"),
                           ("is your offline brain ready", "ai_status"), ("forget our conversation", "ai_forget"),
                           ("link to tab", "tab_link_help")):
        assert B.parse_os(phrase)[0] == action, phrase
    b, said = _brain(tmp_path); b.ai = FakeAI()
    b.handle("forget our conversation"); assert b.ai.asked == ["<forget>"] and said[-1] == "Fresh start, sir."
    b.handle("download your offline brain"); assert "already here" in said[-1]
    b.ai = FakeAI(ready=False); b.handle("download your offline brain"); assert said[-1].startswith("Downloading my offline brain")
