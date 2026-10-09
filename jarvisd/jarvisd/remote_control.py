"""Tab remote control for JARVIS OS: who may drive, what needs the owner's OK, and the instant kill switch.

States: OFF -> (the owner says "let Tab drive" or approves Tab's request) -> DRIVING -> (kill switch) -> OFF.
While DRIVING:
  * the on-screen indicator ("TAB IS DRIVING") is shown on every screen (indicator.py), and the tray turns pink;
  * every action Tab sends is classified: SAFE runs, ASK waits for the owner's spoken/clicked yes, BLOCK never runs;
  * destructive, purchase and messaging actions are always ASK, even inside a granted session;
  * sessions expire (default 30 min) and end on screen lock, logout, or the kill switch.
Kill switch: Meta+Shift+Esc (KDE global shortcut -> `jarvisctl takeback`), the tray button, or saying
"take back control" / "stop Tab". It flips the state file first (so in-flight actions see it), drops queued
actions, stops the input-injection service (ydotoold), and hides the indicator. It needs no network.
"""
from __future__ import annotations
import json, os, re, time, threading

SAFE, ASK, BLOCK = "safe", "ask", "block"

# action types Tab can send (the v5 sealed-action vocabulary plus Linux additions)
SAFE_TYPES = {"open_app", "open_url", "focus", "screenshot", "read_screen", "list_files", "open_newest", "media",
              "volume", "system_info", "scroll", "click", "keys", "type", "rgb"}
ASK_TYPES = {"delete", "uninstall", "install", "power", "empty_bin", "run_script", "move_files", "settings_change",
             "send_message", "post", "purchase", "update_os", "switch_os"}
BLOCK_TYPES = {"powershell", "shell", "sudo", "disable_kill_switch", "change_password", "read_passwords", "format_disk"}

PURCHASE_URL = re.compile(r"(checkout|/cart|/buy|purchase|/pay(?:ment)?\b|billing|store\.steampowered\.com/(?:checkout|cart)|"
                          r"paypal\.com|stripe\.com|/order|add-to-cart|gift ?card)", re.I)
MESSAGE_APPS = re.compile(r"\b(discord|messages?|mail|gmail|outlook|whatsapp|telegram|signal|slack|instagram|tiktok|x\.com|twitter|reddit)\b", re.I)
SEND_KEYS = re.compile(r"^(enter|ctrl\+enter|return)$", re.I)
DANGER_WORDS = re.compile(r"\b(rm|del|delete|format|wipe|erase|uninstall|shred|mkfs|dd|buy|purchase|pay|checkout|send|post|confirm order)\b", re.I)

def classify(action, context=None):
    """Return (verdict, reason). `context` may carry the focused app/window title (e.g. 'Discord')."""
    t = str(action.get("type", "")).lower()
    ctx = " ".join(str(v) for v in (context or {}).values())
    if t in BLOCK_TYPES:
        return BLOCK, f"{t} is never allowed remotely"
    if t in ASK_TYPES:
        kind = "purchase" if t == "purchase" else "messaging" if t in ("send_message", "post") else "destructive or system change"
        return ASK, kind
    if t == "open_url" and PURCHASE_URL.search(str(action.get("url", ""))):
        return ASK, "purchase page"
    if t == "type":
        if DANGER_WORDS.search(str(action.get("text", ""))) and MESSAGE_APPS.search(ctx):
            return ASK, "typing into a messaging app"
    if t == "keys":
        spec = str(action.get("keys", ""))
        if SEND_KEYS.match(spec) and MESSAGE_APPS.search(ctx):
            return ASK, "this would send a message"
        if PURCHASE_URL.search(ctx) and SEND_KEYS.match(spec):
            return ASK, "this could confirm a purchase"
    if t == "click":
        label = str(action.get("label", ""))
        if DANGER_WORDS.search(label) or PURCHASE_URL.search(ctx):
            return ASK, f"button '{label}' needs your OK"
    if t in SAFE_TYPES:
        return SAFE, "ok"
    return BLOCK, f"unknown action type {t!r}"

class RemoteControl:
    def __init__(self, state_path, on_change=None, clock=time.time, stop_injection=None):
        self.path = state_path
        self.on_change = on_change or (lambda st: None)
        self.clock = clock
        self.stop_injection = stop_injection or (lambda: None)
        self.lock = threading.Lock()
        self.queue = []
        self.audit = []
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                self.st = json.load(f)
        except Exception:
            self.st = {"state": "off"}
        if self.st.get("state") == "driving" and self.clock() > self.st.get("expires", 0):
            self.st = {"state": "off", "ended": "expired"}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.st, f)
        os.replace(tmp, self.path)          # atomic: readers see old or new, never half

    @property
    def driving(self):
        self._load()
        return self.st.get("state") == "driving"

    def grant(self, minutes=30, by="voice"):
        with self.lock:
            self.st = {"state": "driving", "since": self.clock(), "expires": self.clock() + minutes * 60, "granted_by": by}
            self._save(); self.audit.append(("grant", by, minutes))
        self.on_change(self.st)
        return self.st

    def takeback(self, by="kill switch"):
        """Instant, local, idempotent. Order matters: state file first, then queue, then injection, then UI."""
        with self.lock:
            self.st = {"state": "off", "ended": by, "at": self.clock()}
            self._save()
            dropped = len(self.queue); self.queue.clear()
            self.audit.append(("takeback", by, dropped))
        try:
            self.stop_injection()
        finally:
            self.on_change(self.st)
        return dropped

    def submit(self, action, context=None, ask=None, execute=None):
        """Run one action from Tab. ask(question)->bool asks the owner. Returns (status, reason)."""
        if not self.driving:
            self.audit.append(("refused", action.get("type"), "not driving"))
            return "refused", "Tab is not driving right now"
        verdict, why = classify(action, context)
        if verdict == BLOCK:
            self.audit.append(("blocked", action.get("type"), why)); return "blocked", why
        if verdict == ASK:
            ok = bool(ask and ask(f"Tab wants to {action.get('type').replace('_', ' ')} ({why}). Allow it, sir?"))
            if not ok:
                self.audit.append(("declined", action.get("type"), why)); return "declined", why
        if not self.driving:                 # kill switch pressed while we were asking
            return "refused", "control was taken back"
        if execute:
            execute(action)
        self.audit.append(("ran", action.get("type"), why))
        return "ran", why
