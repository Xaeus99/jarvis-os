"""JARVIS's voice on Linux: the same local Piper voice as Windows (en_GB-alan-medium by default), played through
PipeWire. Text never goes to a cloud voice. The voice model is fetched once into ~/.local/share/jarvis/voices
(about 60 MB); until it is there, or if Piper fails, espeak-ng speaks instead so JARVIS is never mute."""
from __future__ import annotations
import io, logging, os, subprocess, tempfile, threading, urllib.request, wave

log = logging.getLogger("jarvisd.voice")
VOICES = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "jarvis", "voices")
HF = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"

def voice_url(name):
    """en_GB-alan-medium -> .../en/en_GB/alan/medium/en_GB-alan-medium.onnx"""
    lang, who, quality = name.split("-", 2)
    return f"{HF}/{lang.split('_')[0]}/{lang}/{who}/{quality}/{name}.onnx"

def ensure_voice(name, folder=VOICES, fetch=urllib.request.urlretrieve):
    path = os.path.join(folder, name + ".onnx")
    if os.path.exists(path) and os.path.exists(path + ".json"):
        return path
    os.makedirs(folder, exist_ok=True)
    for url, dest in ((voice_url(name) + ".json", path + ".json"), (voice_url(name), path)):
        fetch(url, dest + ".part")
        os.replace(dest + ".part", dest)
    return path

class Speaker:
    """speak(text) blocks until the sentence is played (so the mic isn't listening to JARVIS himself)."""
    def __init__(self, cfg=None, run=subprocess.run, load_piper=None):
        a = (cfg or {}).get("assistant", {})
        self.voice_name = a.get("piper_voice", "en_GB-alan-medium")
        self.length_scale = 1.0 / float(a.get("piper_speed", 1.0) or 1.0)
        self.run = run
        self.piper = None
        self.lock = threading.Lock()
        self._load_piper = load_piper or self._default_load
        threading.Thread(target=self._prepare, name="voice-prep", daemon=True).start()

    def _default_load(self, path):
        from piper import PiperVoice
        return PiperVoice.load(path)

    def _prepare(self):
        try:
            self.piper = self._load_piper(ensure_voice(self.voice_name))
            log.info("Piper voice ready: %s", self.voice_name)
        except Exception as e:
            log.warning("Piper voice not available yet (%r); using espeak-ng", e)

    def render(self, text):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            try:
                from piper import SynthesisConfig
                self.piper.synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=self.length_scale))
            except ImportError:
                self.piper.synthesize(text, wf, length_scale=self.length_scale)
        return buf.getvalue()

    def speak(self, text):
        text = (text or "").strip()
        if not text:
            return
        with self.lock:
            if self.piper is not None:
                try:
                    data = self.render(text)
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                        f.write(data); path = f.name
                    try:
                        for player in (["pw-play", path], ["paplay", path], ["aplay", "-q", path]):
                            try:
                                if self.run(player, capture_output=True, timeout=120).returncode == 0:
                                    return
                            except FileNotFoundError:
                                continue
                    finally:
                        os.unlink(path)
                except Exception as e:
                    log.warning("Piper failed (%r); espeak-ng instead", e)
            try:
                self.run(["espeak-ng", "-v", "en-gb", "-s", "165", text], capture_output=True, timeout=60)
            except Exception as e:
                log.error("no voice output at all: %r", e)
