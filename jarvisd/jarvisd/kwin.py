"""Open windows on KDE Plasma (Wayland) through KWin's own scripting interface.

On Wayland one program can't see another's windows, but the compositor (KWin) can, and it runs small JavaScript
"KWin scripts" on request. This module loads a one-shot script that reads `workspace.windowList()` and calls back
over D-Bus to a short-lived name we own, then unloads it (the same technique kdotool uses).

    list_windows()            -> [{"title", "app", "pid", "minimized", "active", "id"}] (front-most/active first)
    active_window()           -> the focused window's dict, or None
    focus(title=None, app=None) -> title of the window brought to the front, or raises LookupError

The D-Bus work runs in a child process (`python -m jarvisd.kwin list|focus ...`) so the voice daemon's threads never
host a Qt event loop. Without KWin (another desktop, no session bus, a test machine) KWinUnavailable is raised and
callers fall back to a process list, the same way Windows JARVIS does off Windows.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, time, uuid

class KWinUnavailable(RuntimeError):
    pass

SINK_IFACE = "org.jarvisos.WindowSink"

def script_source(service, mode="list", title="", app=""):
    """The KWin script. Plasma 6 names: workspace.windowList()/activeWindow; Plasma 5 fallbacks clientList()/activeClient."""
    q = json.dumps({"mode": mode, "title": (title or "").lower(), "app": (app or "").lower()})
    return """
const q = %s;
const ws = workspace;
const all = ws.windowList ? ws.windowList() : ws.clientList();
const act = ws.activeWindow !== undefined ? ws.activeWindow : ws.activeClient;
const out = [];
let hit = null;
for (let i = all.length - 1; i >= 0; i--) {          // stacking order: last = top
  const w = all[i];
  if (!w || !w.normalWindow || w.skipTaskbar || w.skipSwitcher) continue;
  const d = {title: String(w.caption || ""), app: String(w.resourceClass || w.desktopFileName || ""),
             pid: Number(w.pid || 0), minimized: !!w.minimized, active: w === act,
             id: String(w.internalId || "")};
  out.push(d);
  if (q.mode === "focus" && !hit) {
    const t = d.title.toLowerCase(), a = d.app.toLowerCase();
    if ((q.title && t.indexOf(q.title) >= 0) || (q.app && (a === q.app || a.indexOf(q.app) >= 0))) hit = [w, d];
  }
}
if (hit) {
  if (hit[0].minimized) hit[0].minimized = false;
  if (ws.activeWindow !== undefined) ws.activeWindow = hit[0]; else ws.activeClient = hit[0];
}
callDBus("%s", "/", "%s", "windows", JSON.stringify({windows: out, focused: hit ? hit[1].title : null}));
""" % (q, service, SINK_IFACE)

def parse(payload):
    d = json.loads(payload) if isinstance(payload, str) else payload
    wins = [w for w in d.get("windows", []) if isinstance(w, dict)]
    wins.sort(key=lambda w: not w.get("active"))      # stable: active first, then stacking order (top first)
    return {"windows": wins, "focused": d.get("focused")}

# ------------------------------------------------------------------ child process side (owns the Qt loop)
def _run_script(mode, title="", app="", timeout=4.0):
    from PySide6 import QtCore, QtDBus
    app_ = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    bus = QtDBus.QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise KWinUnavailable("no session D-Bus")
    service = "org.jarvisos.WindowList.p%d_%s" % (os.getpid(), uuid.uuid4().hex[:6])
    box = {}

    @QtCore.ClassInfo({"D-Bus Interface": SINK_IFACE})
    class Sink(QtCore.QObject):
        @QtCore.Slot(str)
        def windows(self, payload):
            box["payload"] = payload

    sink = Sink()
    if not bus.registerService(service) or not bus.registerObject("/", sink, QtDBus.QDBusConnection.ExportAllSlots):
        raise KWinUnavailable("couldn't register the callback name on D-Bus")
    kwin = QtDBus.QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus)
    if not kwin.isValid():
        raise KWinUnavailable("KWin isn't running (not a Plasma session?)")
    name = "jarvis-windows-" + uuid.uuid4().hex[:8]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script_source(service, mode, title, app)); path = f.name
    try:
        reply = kwin.call("loadScript", path, name)
        args = reply.arguments()
        if reply.type() == QtDBus.QDBusMessage.ErrorMessage or not args:
            raise KWinUnavailable("KWin refused the script: " + reply.errorMessage())
        sid = int(args[0])
        for obj in ("/Scripting/Script%d" % sid, "/%d" % sid):          # Plasma 6 path, then Plasma 5's
            s = QtDBus.QDBusInterface("org.kde.KWin", obj, "org.kde.kwin.Script", bus)
            if s.isValid():
                s.asyncCall("run"); break          # async: KWin may call us back before it replies
        end = time.monotonic() + timeout
        while "payload" not in box and time.monotonic() < end:
            app_.processEvents(QtCore.QEventLoop.AllEvents, 50)
            time.sleep(0.02)
    finally:
        try: kwin.call("unloadScript", name)
        except Exception: pass
        os.unlink(path)
    if "payload" not in box:
        raise KWinUnavailable("KWin didn't answer")
    return parse(box["payload"])

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else "list"
    title = argv[argv.index("--title") + 1] if "--title" in argv else ""
    app = argv[argv.index("--app") + 1] if "--app" in argv else ""
    try:
        print(json.dumps(_run_script("focus" if mode == "focus" else "list", title, app)))
        return 0
    except KWinUnavailable as e:
        print(json.dumps({"error": str(e)})); return 3
    except Exception as e:                       # PySide6 missing, odd D-Bus states
        print(json.dumps({"error": repr(e)[:200]})); return 3

# ------------------------------------------------------------------ caller side (any thread)
def _child(args, run=subprocess.run, timeout=8):
    env = dict(os.environ); env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        p = run([sys.executable, "-m", "jarvisd.kwin"] + args, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KWinUnavailable(f"window list helper failed: {e!r}")
    try:
        d = json.loads((p.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise KWinUnavailable("window list helper gave no answer")
    if "error" in d:
        raise KWinUnavailable(d["error"])
    return d

def list_windows(run=subprocess.run):
    return _child(["list"], run)["windows"]

def active_window(run=subprocess.run):
    return next((w for w in list_windows(run) if w.get("active")), None)

def focus(title=None, app=None, run=subprocess.run):
    if not (title or app):
        raise ValueError("focus needs a window title or an app name")
    args = ["focus"] + (["--title", title] if title else []) + (["--app", app] if app else [])
    d = _child(args, run)
    if not d.get("focused"):
        raise LookupError(f"no open window matches {title or app!r}")
    return d["focused"]

if __name__ == "__main__":
    sys.exit(main())
