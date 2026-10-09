"""jarvisd main loop: microphone -> wake word ("Hey Jarvis" / "Jarvis") -> record the command -> whisper ->
LinuxBrain -> spoken answer (Piper TTS) + HUD. Audio never leaves the PC; only phrases that are not local
commands go to Tab (when a Tab link is configured).

The loop is written against small interfaces so it runs in tests with arrays instead of a microphone:
  source.read() -> int16 chunk of 1280 samples (80 ms @ 16 kHz) or None at end
  wake.score(chunk) -> (fired, info)          (core/wake.py, the same engine as Windows v5.1)
  stt(audio_int16) -> text                     (faster-whisper on the real system)
  brain.handle(text) -> reply text
"""
from __future__ import annotations
import json, logging, os, sys, time
import numpy as np

log = logging.getLogger("jarvisd")
RATE, CHUNK = 16000, 1280

def record_command(source, prefix=None, max_s=7.0, silence_s=0.9, start_timeout_s=4.0, thresh=500):
    """Collect audio after the wake word until ~0.9 s of silence (simple energy gate)."""
    chunks = [prefix] if prefix is not None and len(prefix) else []
    heard = bool(chunks); quiet = 0.0; waited = 0.0; total = 0.0
    while total < max_s:
        c = source.read()
        if c is None:
            break
        chunks.append(c); dt = len(c) / RATE; total += dt
        loud = float(np.sqrt(np.mean(c.astype(np.float32) ** 2))) > thresh
        if loud:
            heard = True; quiet = 0.0
        elif heard:
            quiet += dt
            if quiet >= silence_s:
                break
        else:
            waited += dt
            if waited >= start_timeout_s:
                break
    return np.concatenate(chunks) if chunks else np.zeros(0, np.int16)

class Daemon:
    def __init__(self, source, wake, stt, brain, chime=None, max_commands=None):
        self.source, self.wake, self.stt, self.brain = source, wake, stt, brain
        self.chime = chime or (lambda: None)
        self.max_commands = max_commands
        self.heard = []

    def run(self):
        n = 0
        while True:
            c = self.source.read()
            if c is None:
                return n
            fired, info = self.wake.score(c)
            if not fired:
                continue
            self.chime()
            audio = record_command(self.source, prefix=(info or {}).get("tail"))
            text = (self.stt(audio) or "").strip()
            self.wake.reset()
            if not text:
                self.brain.say(f"I didn't catch that, {self.brain.sir}.")
                continue
            self.heard.append(text)
            self.brain.handle(text)
            n += 1
            if self.max_commands and n >= self.max_commands:
                return n

class MicSource:
    """sounddevice (PortAudio on PipeWire) microphone, 16 kHz mono int16."""
    def __init__(self, device=None):
        import queue, sounddevice as sd
        self.q = queue.Queue(maxsize=200)
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK, device=device,
                                     callback=lambda d, f, t, s: self.q.put_nowait(d[:, 0].copy()))
        self.stream.start()
    def read(self):
        return self.q.get()

def main():
    logging.basicConfig(level=logging.INFO)
    from .brain import LinuxBrain
    from .core.wake import Wake
    cfg_path = os.path.join(os.path.expanduser("~"), ".config", "jarvis", "config.json")
    cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    from faster_whisper import WhisperModel
    whisper = WhisperModel(cfg.get("whisper_model", "base.en"), device="cpu", compute_type="int8")
    models = os.environ.get("JARVIS_MODELS", "/usr/lib/jarvis-os/models")
    wake = Wake(models, cfg.get("assistant", {}), whisper)
    def stt(audio):
        segs, _ = whisper.transcribe(audio.astype(np.float32) / 32768.0, language="en", beam_size=1)
        return " ".join(s.text for s in segs)
    brain = LinuxBrain(cfg)
    Daemon(MicSource(cfg.get("mic_device")), wake, stt, brain).run()

if __name__ == "__main__":
    sys.exit(main())
