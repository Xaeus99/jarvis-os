"""rc10: a short original sound for each colour protocol (the Windows 7.7 idea, synthesized here with numpy - no sound files
shipped, nothing downloaded). A soft crackle, a rising sweep and a two-note swell; the pitch follows the protocol's colour.
It plays only with the protocol's effect (never during a game) and never with "<colour> theme" (quiet recolour).
"protocol sounds off" / "protocol sounds on" keep the choice in the config ("protocol_sound"); "protocol_volume" 0-100."""
from __future__ import annotations
import logging, os, shutil, subprocess, wave

log = logging.getLogger("jarvisd.protosound")
VERSION = 1
SR = 22050
# theme -> (base pitch Hz, chord ratio, crackle amount); unknown themes use the default
VOICES = {
    "vibranium": (196.0, 1.5, 0.5), "mark3": (164.8, 1.335, 0.7), "cyan": (246.9, 1.5, 0.35),
    "hulkbuster": (220.0, 1.26, 0.6), "ultron": (130.8, 1.189, 0.9), "stealth": (110.0, 2.0, 0.25),
}
DEFAULT = (185.0, 1.5, 0.5)

def synth(theme, seconds=1.5, sr=SR, volume=60):
    """-> int16 numpy array (mono). Deterministic for a theme (seeded), so the cached file never changes."""
    import numpy as np
    f0, ratio, crack = VOICES.get(theme, DEFAULT)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(sum(map(ord, theme or "x")))
    # 1) crackle: sparse clicks through a decaying envelope (first 0.45 s)
    clicks = (rng.random(n) < 0.012 * crack).astype(float) * rng.uniform(-1, 1, n)
    clicks *= np.exp(-t / 0.18) * (t < 0.45)
    # 2) sweep: from f0/2 up to f0*2 over 0.6 s, a little detuned pair
    sweep_f = (f0 / 2) * (4 ** np.clip(t / 0.6, 0, 1))
    ph = 2 * np.pi * np.cumsum(sweep_f) / sr
    sweep = (np.sin(ph) + 0.5 * np.sin(ph * 1.004)) * np.clip(t / 0.05, 0, 1) * np.exp(-np.clip(t - 0.55, 0, None) / 0.12)
    # 3) swell: the two-note chord blooms at 0.55 s and fades out
    env = np.clip((t - 0.5) / 0.12, 0, 1) * np.exp(-np.clip(t - 0.7, 0, None) / 0.35)
    swell = (np.sin(2 * np.pi * f0 * 2 * t) + 0.7 * np.sin(2 * np.pi * f0 * 2 * ratio * t)
             + 0.25 * np.sin(2 * np.pi * f0 * 4 * t)) * env
    x = 0.55 * clicks + 0.35 * sweep + 0.45 * swell
    fade = np.ones(n); k = int(0.08 * sr); fade[-k:] = np.linspace(1, 0, k)
    x *= fade
    peak = float(np.max(np.abs(x))) or 1.0
    x = x / peak * 0.9 * max(0, min(100, int(volume))) / 100.0
    return (x * 32767).astype(np.int16)

def cache_dir(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".cache", "jarvis", "sfx")

def wav_path(theme, volume=60, home=None):
    """Write (once) and return the WAV for this theme and volume."""
    d = cache_dir(home); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"protocol-{theme or 'default'}-v{VERSION}-{int(volume)}.wav")
    if not os.path.exists(p):
        data = synth(theme, volume=volume)
        tmp = p + ".tmp"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(data.tobytes())
        os.replace(tmp, p)
    return p

def players():
    return [a for a in (["pw-play"], ["paplay"], ["aplay", "-q"]) if shutil.which(a[0])]

def enabled(cfg):
    return bool((cfg or {}).get("protocol_sound", True))

def play(theme, cfg=None, home=None, popen=None):
    """Start the sound and return at once (the effect runs at the same time). -> the path played, or None."""
    if not enabled(cfg):
        return None
    if popen is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return None                     # unit tests never make noise on the machine running them
    pl = players()
    if not pl:
        log.info("protocol sound: no audio player found")
        return None
    try:
        p = wav_path(theme, (cfg or {}).get("protocol_volume", 60), home)
        (popen or subprocess.Popen)(pl[0] + [p], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        log.info("protocol sound: %s (%s)", os.path.basename(p), pl[0][0])
        return p
    except Exception as e:
        log.info("protocol sound failed: %r", e)
        return None
