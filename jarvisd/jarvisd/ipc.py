"""Tiny local IPC between JARVIS's own processes (jarvisctl, the voice daemon, the workspace panel).

One JSON object per line over a Unix socket in $XDG_RUNTIME_DIR/jarvis/ (a per-user tmpfs, mode 0700), so only
this user's processes can talk to it. Used for single-instance windows: `jarvisctl workspace toggle` (the
Ctrl+Shift+J shortcut) asks the running panel to toggle instead of starting a second one.
"""
from __future__ import annotations
import json, logging, os, socket, tempfile, threading

log = logging.getLogger("jarvisd.ipc")

def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(tempfile.gettempdir(), f"jarvis-{os.getuid()}")
    d = os.path.join(base, "jarvis")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d

def sock_path(name):
    return name if os.sep in name else os.path.join(runtime_dir(), name + ".sock")

def send(name, msg, timeout=2.0):
    """Send one message; returns the decoded reply, or None if nothing is listening."""
    path = sock_path(name)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall((json.dumps(msg) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode() or "null") if buf else {}
    except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError, ValueError):
        return None
    finally:
        s.close()

def alive(name):
    return send(name, {"cmd": "ping"}, timeout=1.0) is not None

class Server:
    """Answers messages on a background thread: handler(msg: dict) -> dict (or None).
    Refuses to start if another live instance owns the socket (raises RuntimeError: single instance)."""
    def __init__(self, name, handler):
        self.path, self.handler = sock_path(name), handler
        self.sock = None; self.thread = None; self._stop = False

    def start(self):
        if os.path.exists(self.path):
            if alive(self.path):
                raise RuntimeError(f"already running ({self.path})")
            os.unlink(self.path)                       # stale socket from a crashed run
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path); os.chmod(self.path, 0o600); self.sock.listen(8)
        self.thread = threading.Thread(target=self._loop, name="ipc-" + os.path.basename(self.path), daemon=True)
        self.thread.start()
        return self

    def _loop(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(5)
                    buf = b""
                    while not buf.endswith(b"\n"):
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                    msg = json.loads(buf.decode() or "{}")
                    if msg.get("cmd") == "ping":
                        reply = {"ok": True, "pid": os.getpid()}
                    else:
                        reply = self.handler(msg) or {"ok": True}
                except Exception as e:                  # a bad message never kills the server
                    log.info("ipc message failed: %r", e)
                    reply = {"ok": False, "error": str(e)[:200]}
                try:
                    conn.sendall((json.dumps(reply) + "\n").encode())
                except OSError:
                    pass

    def close(self):
        self._stop = True
        try:
            if self.sock: self.sock.close()
        finally:
            try: os.unlink(self.path)
            except OSError: pass
