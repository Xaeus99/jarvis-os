"""The local AI race (jarvisd/race.py + LocalAI.ask_stream) against a fake streaming Ollama."""
import json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from jarvisd import localai, race, brain as B

class StreamOllama(BaseHTTPRequestHandler):
    models = set(); delay = {}; chats = []; unloaded = []
    words = "Certainly, sir. A black hole is a region where gravity wins. Nothing escapes it."
    def log_message(self, *a): pass
    def _json(self, d):
        b = json.dumps(d).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/api/version": return self._json({"version": "0.40.2"})
        if self.path == "/api/tags": return self._json({"models": [{"name": m} for m in sorted(self.models)]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/generate":
            StreamOllama.unloaded.append((body["model"], body.get("keep_alive"))); return self._json({"done": True})
        StreamOllama.chats.append(body)
        self.send_response(200); self.end_headers()
        time.sleep(self.delay.get(body["model"], 0))
        for w in (self.words.replace(".", ". " + body["model"] + "|", 1) if body["model"] == "slow" else self.words).split(" "):
            try:
                self.wfile.write((json.dumps({"message": {"content": w + " "}, "done": False}) + "\n").encode()); self.wfile.flush()
            except OSError:
                return
            time.sleep(0.01)
        self.wfile.write((json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode())

@pytest.fixture
def srv():
    StreamOllama.models = set(); StreamOllama.delay = {}; StreamOllama.chats = []; StreamOllama.unloaded = []
    s = ThreadingHTTPServer(("127.0.0.1", 0), StreamOllama)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()

def test_race_models_for_the_hardware():
    assert localai.race_models({}, nvidia=True, ram=8) == ["llama3.2:1b", "llama3.2:3b", "qwen2.5:3b"]   # RTX card
    assert localai.race_models({}, nvidia=False, ram=32) == localai.RACE_MODELS
    assert localai.race_models({}, nvidia=False, ram=8) == ["llama3.2:1b"]                               # small virtual PC
    assert localai.race_models({"assistant": {"local_ai_model": "m"}}) == ["m"]
    assert localai.race_models({"assistant": {"local_ai_models": ["a", "b"]}}) == ["a", "b"]

def test_splitter_speaks_sentences_as_they_complete():
    sp = race.SentenceSplitter(); out = []
    for d in "Hello sir. This is **bold**. And 3.5 is a number. Last".split(" "):
        out += sp.feed(d + " ")
    out += sp.flush()
    assert out == ["Hello sir.", "This is bold.", "And 3.5 is a number.", "Last"]

def test_fastest_model_wins_and_its_sentences_stream(srv):
    StreamOllama.models = {"fast", "slow"}; StreamOllama.delay = {"slow": 1.5}
    ai = localai.LocalAI({"assistant": {"local_ai_url": srv, "local_ai_models": ["slow", "fast"]}})
    assert ai.ready() and ai.available() == ["slow", "fast"]
    got = []
    res = ai.ask_stream("what is a black hole", got.append, decide_timeout=10, total_timeout=20)
    assert res.ok and res.winner == "fast" and res.first_s < 1.5
    assert got[0] == "Certainly, sir." and len(got) == 3 and res.text.startswith("Certainly")
    assert {c["model"] for c in StreamOllama.chats} == {"slow", "fast"}            # both asked at once
    assert StreamOllama.chats[0]["messages"][0]["role"] == "system" and StreamOllama.chats[0]["stream"] is True
    assert ai.history[-1]["content"] == res.text

def test_in_a_game_only_the_small_model_answers(srv):
    StreamOllama.models = {"llama3.2:1b", "llama3.2:3b", "qwen2.5:3b"}
    ai = localai.LocalAI({"assistant": {"local_ai_url": srv, "local_ai_models": localai.RACE_MODELS}})
    assert ai.available(in_game=True) == ["llama3.2:1b"]
    res = ai.ask_stream("hi", lambda s: None, in_game=True)
    assert res.ok and [c["model"] for c in StreamOllama.chats] == ["llama3.2:1b"]

def test_models_not_downloaded_yet_sit_out(srv):
    StreamOllama.models = {"llama3.2:1b"}
    ai = localai.LocalAI({"assistant": {"local_ai_url": srv, "local_ai_models": localai.RACE_MODELS}})
    assert ai.available() == ["llama3.2:1b"]
    StreamOllama.models = set()
    assert not ai.ready() and ai.ask_stream("hi", lambda s: None) is None

def test_unload_frees_the_models(srv):
    localai.unload(["a", "b"], srv)
    assert StreamOllama.unloaded == [("a", 0), ("b", 0)]

def test_brain_speaks_the_race_winner_sentence_by_sentence(srv, tmp_path):
    StreamOllama.models = {"llama3.2:1b"}
    said = []
    b = B.LinuxBrain({"assistant": {"local_ai_url": srv, "local_ai_models": ["llama3.2:1b"]}}, state_dir=str(tmp_path),
                     run=lambda argv, **k: (0, ""), say=lambda t: said.append(t) or t)
    out = b.handle("what is a black hole")
    assert said == ["Certainly, sir.", "A black hole is a region where gravity wins.", "Nothing escapes it."]
    assert out.startswith("Certainly")
