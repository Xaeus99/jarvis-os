"""Wake word for J.A.R.V.I.S.: "Hey Jarvis" AND plain "Jarvis", all on-device.

How it works (two stages, so CPU stays light while gaming):
  1. openWakeWord's hey_jarvis model runs on every 80 ms chunk, exactly as before (~1-2 ms/chunk).
     A high score fires immediately ("Hey Jarvis" path, unchanged behaviour).
  2. Plain "Jarvis" usually gives that same model a small but clear bump. A bump above a
     low trigger threshold starts a short confirm: after a few more chunks, the already-loaded
     faster-whisper model transcribes the last ~1.6 s and we wake only if the words contain
     "Jarvis". Whisper runs only on these bumps (rate-limited), never continuously.
No new dependency, no new model download. Mic audio never leaves the PC.

Config (assistant section of config.json):
  "wake_sensitivity": "low" | "normal" | "high"   (default "normal")
  "bare_jarvis": true | false                       (default true; needs a whisper model)
  "wake_threshold": optional float, overrides the "Hey Jarvis" direct-fire threshold

Engine use:
    wake = Wake(models_dir, acfg, ears.whisper)
    fired, info = wake.score(chunk)        # every 1280-sample int16 chunk
    if fired:  ...  record(prefix=info["tail"]) ... ; wake.reset()
info["tail"] is the int16 audio heard *after* "Jarvis" while the confirm was pending
(0-0.5 s). Prepend it to the command recording so "Jarvis open Spotify" said in one breath
keeps the word "open".
"""
from __future__ import annotations
import collections, logging, os, re, time
import numpy as np

log = logging.getLogger("jarvis.wake")
RATE, CHUNK = 16000, 1280

# hey: direct-fire threshold for "Hey Jarvis" (openWakeWord score).
# trig: openWakeWord score that starts a whisper confirm for plain "Jarvis".
# gap: minimum seconds between whisper confirms (CPU cap).
LEVELS = {
    # v5: a little stricter at every level (v4 normal was hey=0.50, trig=0.005) - fewer false wakes
    "low":    dict(hey=0.65, trig=0.03, gap=1.5),
    "normal": dict(hey=0.55, trig=0.01, gap=1.0),
    "high":   dict(hey=0.45, trig=0.004, gap=0.6),
}
# openWakeWord's score peaks ~0.3-0.6 s AFTER the word ends, and whisper needs that trailing audio
# to hear "Jarvis" rather than "Jar..": confirm once the score has stopped rising for 240 ms.
# So: confirm when the bump is over (score back under the trigger level for 3 chunks) or 1.6 s
# after the trigger; if whisper heard only a "J..." fragment, look once more 0.4 s later.
BELOW_END = 3
MAX_WAIT = 20
RETRY_CHUNKS = 5
J_FRAGMENT = re.compile(r"\b(j\w*|ja|jar\w*)\b", re.I)
REARM_S = 0.4          # a clearly higher new bump (2x the last confirmed peak) may re-confirm this soon
WINDOW_S = 1.8         # audio handed to whisper
REFRACTORY = 12        # chunks after a wake during which nothing fires (same as v2 engine)
# What whisper may write for "Jarvis". Deliberately not "Travis"/"service"/"nervous".
JARVIS_RE = re.compile(r"\b(jarv\w*|jervis|j[ao]vis|jarbis)\b", re.I)

class Wake:
    def __init__(self, models_dir, cfg=None, whisper=None, oww_model=None):
        cfg = cfg or {}
        self.cfg = cfg
        if oww_model is None:
            from openwakeword.model import Model
            ow = os.path.join(models_dir, "openwakeword")
            oww_model = Model(wakeword_models=[os.path.join(ow, "hey_jarvis_v0.1.onnx")],
                              melspec_model_path=os.path.join(ow, "melspectrogram.onnx"),
                              embedding_model_path=os.path.join(ow, "embedding_model.onnx"),
                              inference_framework="onnx")
        self.oww = oww_model
        self.whisper = whisper
        self.set_sensitivity(cfg.get("wake_sensitivity", "normal"))
        self.bare = bool(cfg.get("bare_jarvis", True)) and whisper is not None
        # verify_hey: also whisper-check "Hey Jarvis" fires (kills e.g. "a jar of" false wakes, +~0.3 s latency)
        self.verify_hey = bool(cfg.get("wake_verify_hey", False)) and whisper is not None
        self.buf = collections.deque(maxlen=int(WINDOW_S * RATE / CHUNK) + 2)
        self.stats = collections.Counter()
        self.confirm_ms = 0.0
        self.reset()

    # -- settings ---------------------------------------------------------
    def set_sensitivity(self, level):
        level = str(level or "normal").lower()
        if level not in LEVELS:
            level = "normal"
        self.level = level
        p = dict(LEVELS[level])
        if self.cfg.get("wake_threshold") is not None and level == "normal":
            p["hey"] = float(self.cfg["wake_threshold"])
        self.hey_thr, self.trig_thr, self.gap = p["hey"], p["trig"], p["gap"]

    def reset(self):
        try:
            self.oww.reset()
        except Exception:
            pass
        self.buf.clear()
        self.pending = -1          # chunks left before a confirm, -1 = none
        self.pend_peak = 0.0
        self.tail = []
        self.since = 999           # chunks since last wake
        self.last_confirm = -1e9   # chunk index of last whisper run
        self.last_peak = 0.0
        self.n = 0

    # -- main entry ---------------------------------------------------------
    def score(self, chunk):
        """Feed one 1280-sample int16 chunk. Returns (fired, info)."""
        scores = self.oww.predict(chunk)
        s = max(scores.values()) if scores else 0.0
        return self.step(chunk, s)

    def step(self, chunk, s):
        """Same as score() with the openWakeWord score already computed (used by tests)."""
        self.n += 1; self.since += 1
        self.buf.append(np.asarray(chunk, dtype=np.int16))
        info = {"score": float(s), "how": None}
        if self.since <= REFRACTORY:
            return False, info
        if s >= self.hey_thr and self.verify_hey and self.pending < 0:
            self._start(s)
            return False, info
        if s >= self.hey_thr and not self.verify_hey:
            self.stats["hey"] += 1
            return self._fire(info, "hey", tail=self.tail if self.pending >= 0 else [])
        if not self.bare:
            return False, info
        if self.pending >= 0:
            self.tail.append(self.buf[-1]); self.pending += 1
            if s > self.pend_peak:
                self.pend_peak = s
            self.below = self.below + 1 if s < self.trig_thr else 0
            if self.retry_at is not None:
                if self.pending >= self.retry_at:
                    return self._confirm(info)
            elif self.below >= BELOW_END or self.pending >= MAX_WAIT:
                return self._confirm(info)
            return False, info
        el = (self.n - self.last_confirm) * CHUNK / RATE
        if s >= self.trig_thr and (el >= self.gap or (el >= REARM_S and s >= 2 * self.last_peak)):
            self._start(s)
        return False, info

    def _start(self, s):
        self.pending = 0; self.pend_peak = s; self.below = 0; self.retry_at = None; self.tail = []
        self.stats["triggers"] += 1

    def _confirm(self, info):
        self.last_confirm = self.n; self.last_peak = self.pend_peak
        n_tail = min(len(self.buf), self.pending + 6)   # from ~0.5 s before the trigger
        first, pending_was = self.retry_at is None, self.pending
        self.pending = -1
        audio = np.concatenate(list(self.buf))[-int(WINDOW_S * RATE):].astype(np.float32) / 32768.0
        t0 = time.perf_counter()
        text = self.transcribe_short(audio)
        dt = (time.perf_counter() - t0) * 1000
        self.confirm_ms += dt; self.stats["confirms"] += 1
        info.update(text=text, confirm_ms=dt, peak=self.pend_peak)
        tail = list(self.buf)[-n_tail:]; self.tail = []
        if JARVIS_RE.search(text or ""):
            self.stats["confirmed"] += 1
            return self._fire(info, "jarvis", tail=tail)
        if first and J_FRAGMENT.search(text or ""):
            self.pending, self.retry_at = pending_was, pending_was + RETRY_CHUNKS   # one more look
        log.debug("wake confirm rejected %r (peak %.3f)", text, self.pend_peak)
        return False, info

    def transcribe_short(self, audio):
        segs, _ = self.whisper.transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                          condition_on_previous_text=False, without_timestamps=True,
                                          temperature=0.0)
        return " ".join(seg.text.strip() for seg in segs).strip()

    def _fire(self, info, how, tail):
        info["how"] = how
        info["tail"] = np.concatenate(tail) if len(tail) else np.zeros(0, np.int16)
        self.pending = -1; self.tail = []; self.since = 0
        log.info("wake (%s) score %.3f %s", how, info["score"], info.get("text", ""))
        return True, info
