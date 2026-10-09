"""Wake word + daemon loop. The fake-model tests always run; the real-model test runs when openwakeword,
faster-whisper and espeak-ng are installed (they are on this test box)."""
import os, shutil, subprocess, wave
import numpy as np
import pytest
from jarvisd.core.wake import Wake
from jarvisd.daemon import Daemon, record_command, RATE, CHUNK

class ArraySource:
    def __init__(self, audio):
        self.a = audio; self.i = 0
    def read(self):
        if self.i >= len(self.a): return None
        c = self.a[self.i:self.i + CHUNK]; self.i += CHUNK
        if len(c) < CHUNK: c = np.pad(c, (0, CHUNK - len(c)))
        return c

class FakeOWW:
    """Scores 0.9 on chunks marked loud with value 7777, else 0."""
    def predict(self, chunk):
        return {"hey_jarvis_v0.1": 0.9 if chunk[0] == 7777 else 0.0}
    def reset(self): pass

class FakeBrain:
    sir = "sir"
    def __init__(self): self.got = []; self.said = []
    def handle(self, t): self.got.append(t); return "ok"
    def say(self, t): self.said.append(t)

def tone(sec, amp=3000):
    t = np.arange(int(sec * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16)

def test_record_command_stops_on_silence():
    audio = np.concatenate([tone(1.0), np.zeros(int(2.0 * RATE), np.int16), tone(1.0)])
    got = record_command(ArraySource(audio))
    assert 1.8 * RATE <= len(got) <= 2.2 * RATE      # speech + ~0.9 s silence, not the later tone

def test_daemon_wake_then_command_with_fake_model():
    wake_chunk = np.full(CHUNK, 7777, np.int16)
    audio = np.concatenate([np.zeros(CHUNK * 5, np.int16), wake_chunk, tone(1.0), np.zeros(int(1.5 * RATE), np.int16)])
    brain = FakeBrain()
    d = Daemon(ArraySource(audio), Wake("", {}, None, oww_model=FakeOWW()), lambda a: "set a timer for five minutes", brain)
    assert d.run() == 1 and brain.got == ["set a timer for five minutes"]

def test_silence_never_wakes():
    brain = FakeBrain()
    d = Daemon(ArraySource(np.zeros(RATE * 5, np.int16)), Wake("", {}, None, oww_model=FakeOWW()), lambda a: "x", brain)
    assert d.run() == 0 and brain.got == []

MODELS = os.path.join(os.path.dirname(__file__), "..", "..", "image", "system_files", "usr", "lib", "jarvis-os", "models")

def _speak(text, path, voice="en-us+m3", speed=150):
    subprocess.run(["espeak-ng", "-v", voice, "-s", str(speed), "-w", path, text], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ar", "16000", "-ac", "1", path + ".16k.wav"], check=True)
    with wave.open(path + ".16k.wav") as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)

@pytest.mark.skipif(not (shutil.which("espeak-ng") and shutil.which("ffmpeg")), reason="needs espeak-ng + ffmpeg")
def test_real_pipeline_synthetic_voice(tmp_path):
    pytest.importorskip("openwakeword"); fw = pytest.importorskip("faster_whisper")
    from jarvisd.brain import LinuxBrain
    from conftest import Rec
    whisper = fw.WhisperModel("base.en", device="cpu", compute_type="int8")
    gap = np.zeros(int(0.15 * RATE), np.int16)
    wake_audio = None
    for voice in ("en-us+m3", "en-gb", "en-us"):                 # a robot voice; try a couple
        wake_audio = _speak("Hey Jarvis", str(tmp_path / f"w_{voice}.wav"), voice=voice)
        cmd_audio = _speak("set a timer for five minutes", str(tmp_path / "c.wav"))
        audio = np.concatenate([np.zeros(RATE, np.int16), wake_audio, gap, cmd_audio, np.zeros(int(1.5 * RATE), np.int16)])
        wake = Wake(MODELS, {"wake_sensitivity": "high"}, whisper)
        def stt(a):
            segs, _ = whisper.transcribe(a.astype(np.float32) / 32768.0, language="en", beam_size=1)
            return " ".join(s.text for s in segs)
        brain = LinuxBrain({}, state_dir=str(tmp_path / "st"), run=Rec())
        d = Daemon(ArraySource(audio), wake, stt, brain, max_commands=1)
        if d.run() == 1:
            break
    assert d.heard, "wake word never fired on synthetic 'Hey Jarvis'"
    assert "timer" in d.heard[0].lower()
    assert any("Timer set for 5 minutes" in s for s in brain.spoken), brain.spoken


@pytest.mark.skipif(not (shutil.which("espeak-ng") and shutil.which("ffmpeg")), reason="needs espeak-ng + ffmpeg")
@pytest.mark.parametrize("phrase", ["what is the weather like today", "open the steam library", "hey travis how are you", "jar of pickles please"])
def test_real_model_ignores_non_wake_speech(tmp_path, phrase):
    pytest.importorskip("openwakeword"); fw = pytest.importorskip("faster_whisper")
    whisper = fw.WhisperModel("base.en", device="cpu", compute_type="int8")
    a = _speak(phrase, str(tmp_path / "n.wav"))
    wake = Wake(MODELS, {"wake_sensitivity": "normal"}, whisper)
    src = ArraySource(np.concatenate([np.zeros(RATE, np.int16), a, np.zeros(RATE, np.int16)]))
    while (c := src.read()) is not None:
        fired, _ = wake.score(c)
        assert not fired, phrase
