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
import json, logging, os, sys, threading, time
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

class _NoHud:
    def listening(self): pass
    def thinking(self, heard=""): pass
    def speaking(self, text): pass
    def idle(self): pass

class Daemon:
    def __init__(self, source, wake, stt, brain, chime=None, max_commands=None, hud=None):
        self.source, self.wake, self.stt, self.brain = source, wake, stt, brain
        self.chime = chime or (lambda: None)
        self.max_commands = max_commands
        self.hud = hud or _NoHud()
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
            self.chime(); self.hud.listening()
            audio = record_command(self.source, prefix=(info or {}).get("tail"))
            text = (self.stt(audio) or "").strip()
            self.wake.reset()
            if not text:
                self.brain.say(f"I didn't catch that, {self.brain.sir}.")
                self.hud.idle()
                continue
            self.heard.append(text)
            self.hud.thinking(text)
            self.brain.handle(text)
            self.hud.idle()
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

def open_mic(device=None, wait_s=30.0, make=None, sleep=time.sleep):
    """Open the microphone, waiting for one if there is none yet (a PC without a mic, or a headset not plugged in).
    Before: jarvisd exited and systemd restarted it every few seconds, re-loading the speech model each time."""
    make = make or MicSource
    said = False
    while True:
        try:
            return make(device)
        except Exception as e:
            if not said:
                log.warning("no microphone yet (%s); JARVIS starts listening as soon as one is plugged in", e)
                said = True
            sleep(wait_s)

def wire_tab(cfg, brain, speak, state_dir, listener_cls=None, send_result=None):
    """Connect the brain to Tab: forward unknown phrases, speak Tab's replies, run Tab's (safe) actions and send the
    sealed results back. Returns (forwarder, listener); both None when this PC isn't linked yet."""
    from . import tablink
    from .core import replies
    fcfg, rcfg = cfg.get("forwarding") or {}, cfg.get("replies") or {}
    fwd = tablink.Forwarder(fcfg, os.path.join(state_dir, "forward.json"))
    if not fwd.configured:
        log.info("not linked to Tab yet: run `jarvisctl link --from-windows` (or add forwarding/replies to the config)")
        return None, None
    runner = tablink.ActionRunner(brain.hands, speak, allowed=lambda: cfg.get("tab_actions", True) is not False)
    send_result = send_result or tablink.send_result
    def on_reply(text, msg):
        if text and not msg.get("stale"):
            speak(text)
        if msg.get("actions") is not None:
            res = runner.run(msg)
            log.info("Tab's actions: %s -> %s", res.get("status"), send_result(fcfg, rcfg, res))
    listener = None
    if rcfg.get("topic") and rcfg.get("key") and rcfg.get("enabled", True):
        listener = (listener_cls or replies.ReplyListener)(rcfg, os.path.join(state_dir, "replies.json"), on_reply)
    def forward(text):
        route, detail = fwd.send(text, context=list(brain.spoken[-3:]))
        log.info("to Tab via %s (%s)", route, detail)
        if listener is not None:
            listener.boost()
    brain.forward = forward
    return fwd, listener

def main():
    logging.basicConfig(level=logging.INFO)
    from .brain import LinuxBrain
    from .core.wake import Wake
    from .hud import HudBus
    from .voice_out import Speaker
    cfg_path = os.path.join(os.path.expanduser("~"), ".config", "jarvis", "config.json")
    cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    hud, voice = HudBus(), Speaker(cfg)
    def speak(text):
        hud.speaking(text); voice.speak(text); hud.idle()
    brain = LinuxBrain(cfg, say=speak)
    def warm_obs():                       # OBS already open at login: keep its replay buffer on for "clip that"
        try: brain.studio.warm()
        except Exception as e: log.info("OBS warm-up: %r", e)
    threading.Thread(target=warm_obs, name="obs-warm", daemon=True).start()
    try:                                  # games: HUD stays out of the way, camera greeting pauses, auto game mode
        from . import gamewatch
        brain.gamewatch = gamewatch.wire(cfg, brain.run, hud=hud).start()
    except Exception as e:
        log.info("game watcher unavailable: %r", e)
    try:                                  # "welcome back, sir" (paused in games and while OBS/Discord use the webcam)
        from . import camera
        gw = getattr(brain, "gamewatch", None)
        brain.camera = camera.start(cfg, brain, speak, is_gaming=lambda: bool(gw and gw.gaming))
    except Exception as e:
        log.info("camera greeting unavailable: %r", e)
    _, listener = wire_tab(cfg, brain, speak, brain.state_dir)
    if listener is not None:
        listener.start()          # Tab's replies are spoken even while there's no microphone
    mic = open_mic(cfg.get("mic_device"))
    from faster_whisper import WhisperModel
    whisper = WhisperModel(cfg.get("whisper_model", "base.en"), device="cpu", compute_type="int8")
    models = os.environ.get("JARVIS_MODELS", "/usr/lib/jarvis-os/models")
    wake = Wake(models, cfg.get("assistant", {}), whisper)
    def stt(audio):
        segs, _ = whisper.transcribe(audio.astype(np.float32) / 32768.0, language="en", beam_size=1)
        return " ".join(s.text for s in segs)
    Daemon(mic, wake, stt, brain, hud=hud).run()

if __name__ == "__main__":
    sys.exit(main())
