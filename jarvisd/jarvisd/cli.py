"""jarvisctl: talk to JARVIS from a terminal or a KDE shortcut.
  jarvisctl say "set a timer for 5 minutes"   run one phrase through the brain (prints the spoken reply)
  jarvisctl takeback                          the kill switch (bound to Meta+Shift+Esc)
  jarvisctl status                            remote-control state + OS image versions
  jarvisctl apps install                      install the JARVIS OS app list from Flathub (first boot does this once)
  jarvisctl games restore [--mount C=/path]   queue this PC's Windows Steam games for download on Linux
  jarvisctl link --from-windows               link to the same Tab as Windows JARVIS (reads the Windows drive read-only)
  jarvisctl link --from-windows --bitlocker   same, when Windows is BitLocker-encrypted (asks for its 48-digit recovery key)
  jarvisctl link --code 7KQ2-M9XD-R4TB        link with a one-time code from Tab (no Windows needed)
  jarvisctl workspace [toggle|open|close|status]  the workspace panel (Ctrl+Shift+J = toggle)
  jarvisctl --dry-run say "switch to windows" print the OS commands instead of running them"""
from __future__ import annotations
import argparse, json, sys

from .brain import LinuxBrain
from .linux_hands import Runner

class DryRun:
    def __init__(self): self.calls = []
    def __call__(self, argv, timeout=15, input_text=None):
        self.calls.append(argv); print("would run:", " ".join(argv))
        if argv[0] == "efibootmgr" and len(argv) == 1:
            return 0, "BootCurrent: 0001\nBootOrder: 0001,0000\nBoot0000* Windows Boot Manager\tHD(1,GPT,...)\nBoot0001* JARVIS OS\tHD(1,GPT,...)\n"
        if argv[0] == "wpctl" and argv[1] == "get-volume":
            return 0, "Volume: 0.40"
        return 0, ""

def main(argv=None):
    ap = argparse.ArgumentParser(prog="jarvisctl")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="answer yes to confirmations (testing only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("say"); p.add_argument("text")
    sub.add_parser("takeback"); sub.add_parser("status")
    p = sub.add_parser("apps"); p.add_argument("what", choices=["install"]); p.add_argument("--list", default=None)
    p.add_argument("--first-boot", action="store_true")
    p = sub.add_parser("games"); p.add_argument("what", choices=["restore", "list"])
    p.add_argument("--mount", action="append", default=[], help="LETTER=/path of an already-mounted Windows drive")
    p.add_argument("--steamapps", default=None)
    p = sub.add_parser("link"); g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--from-windows", action="store_true"); g.add_argument("--code", default=None, help="one-time link code from Tab")
    p.add_argument("--bitlocker", action="store_true", help="Windows is BitLocker-encrypted: unlock it read-only with its recovery key")
    p.add_argument("--windows-root", default=None, help="an already-mounted Windows drive (default: mount it read-only)")
    p.add_argument("--config", default=None); p.add_argument("--first-boot", action="store_true")
    p = sub.add_parser("dnd-hold", help="hold Do Not Disturb until stopped (used by stream mode)"); p.add_argument("reason", nargs="?", default="Streaming")
    p = sub.add_parser("workspace", help="the workspace panel (Ctrl+Shift+J runs `jarvisctl workspace toggle`)")
    p.add_argument("what", nargs="?", default="toggle", choices=["toggle", "open", "close", "status"])
    p = sub.add_parser("ai", help="the offline brain (local AI chat): setup downloads it, status, ask \"...\"")
    p.add_argument("what", choices=["setup", "status", "ask", "race"]); p.add_argument("text", nargs="?", default="")
    p.add_argument("--in-game", action="store_true", help="race: as during a game (small model only)")
    p.add_argument("--first-boot", action="store_true")
    p = sub.add_parser("tell", help="say something to the running JARVIS (as if spoken); it answers out loud")
    p.add_argument("text")
    p = sub.add_parser("make", help="make code or an image locally (what \"Jarvis, write code for ...\" / \"make an image of ...\" do)")
    p.add_argument("what", choices=["code", "image", "status", "setup"]); p.add_argument("text", nargs="?", default="")
    p.add_argument("--no-viewer", action="store_true", help="don't open the JARVIS viewer")
    sub.add_parser("windows", help="list the open windows (asks KWin)")
    sub.add_parser("listen", help="make the running JARVIS listen now (push-to-talk; bound to Ctrl+Shift+Space)")
    a = ap.parse_args(argv)
    if a.cmd == "ai":
        return ai_cmd(a)
    if a.cmd == "make":
        return make_cmd(a)
    if a.cmd == "windows":
        from . import kwin
        try:
            for w in kwin.list_windows():
                print(("* " if w.get("active") else "  ") + f"{w.get('app') or '?'}: {w.get('title')}" + (" [minimized]" if w.get("minimized") else ""))
            return 0
        except kwin.KWinUnavailable as e:
            print("No window list:", e); return 1
    if a.cmd in ("tell", "listen"):
        from . import ipc
        r = ipc.send("daemon", {"cmd": "say", "text": a.text} if a.cmd == "tell" else {"cmd": "listen"})
        print(json.dumps(r if r is not None else {"ok": False, "error": "JARVIS's voice service isn't running (systemctl --user start jarvisd)"}))
        return 0 if r and r.get("ok") else 1
    if a.cmd == "link":
        return link(a)
    if a.cmd == "workspace":
        return workspace_cmd(a)
    if a.cmd == "dnd-hold":
        from .gaming import dnd_hold
        return dnd_hold(a.reason)
    run = DryRun() if a.dry_run else Runner()
    b = LinuxBrain(run=run, ask=(lambda q: (print("JARVIS asks:", q), "yes")[1]) if a.yes else (lambda q: (print("JARVIS asks:", q), "")[1]))
    if a.cmd == "say":
        print("JARVIS:", b.handle(a.text))
    elif a.cmd == "takeback":
        n = b.remote.takeback(by="jarvisctl"); print(f"control taken back ({n} queued actions dropped)")
    elif a.cmd == "apps":
        return apps_install(run, a)
    elif a.cmd == "games":
        return games(run, a)
    elif a.cmd == "status":
        print(json.dumps({"remote": b.remote.st}, indent=1))
    return 0

def ai_cmd(a):
    import os
    from . import localai
    cfg_path = os.path.expanduser("~/.config/jarvis/config.json")
    try:
        cfg = json.load(open(cfg_path))
    except (OSError, ValueError):
        cfg = {}
    if a.what == "setup":
        st = localai.setup(cfg)
        print(json.dumps(st))
        if st.get("status") in ("ready", "off") and a.first_boot:
            done = os.path.join(os.path.dirname(localai.STATE), "localai.done")
            open(done, "w").write(st.get("status") + "\n")
        return 0 if st.get("status") in ("ready", "off") else 1
    ai = localai.LocalAI(cfg)
    if a.what == "status":
        print(json.dumps({"ready": ai.ready(), "model": ai.model(), **localai.read_state()}))
        return 0
    if a.what == "race":
        import time as _t
        t0 = _t.monotonic()
        res = ai.ask_stream(a.text or "Introduce yourself in one sentence.",
                            lambda s: print(f"[{_t.monotonic() - t0:5.1f}s] {s}", flush=True), in_game=a.in_game)
        if res is None:
            print("(the offline brain isn't ready: jarvisctl ai setup)"); return 1
        print(json.dumps({"winner": res.winner, "first_sentence_s": res.first_s, "racers": ai.available(a.in_game),
                          "errors": res.errors}))
        return 0 if res.ok else 1
    ans = ai.ask(a.text or "Introduce yourself in one sentence.")
    print("JARVIS:", ans if ans else "(the offline brain isn't ready: jarvisctl ai setup)")
    return 0 if ans else 1

def make_cmd(a):
    """Blocking version of the voice commands, for scripts and tests. Prints one JSON line with the result."""
    import os, time
    from . import creator, safety
    try:
        cfg = json.load(open(os.path.expanduser("~/.config/jarvis/config.json")))
    except (OSError, ValueError):
        cfg = {}
    show = (lambda k, p: None) if a.no_viewer else None
    if a.what in ("status", "setup"):
        img, code = creator.ImageMaker(cfg, show=show), creator.CodeMaker(cfg, show=show)
        if a.what == "setup":
            img.install(progress=lambda label, pct: print(f"{label} {pct}%", flush=True) if pct % 25 == 0 else None)
            code.ensure_model()
        print(json.dumps({"images": img.installed(), "image_build": img.build, "code": code.have_model()}))
        return 0
    t0 = time.monotonic()
    if a.what == "code":
        if not safety.check_code_request(a.text):
            print(json.dumps({"result": "refused"})); return 1
        code = creator.CodeMaker(cfg, show=show)
        if not code.ensure_model():
            print(json.dumps({"result": "failed", "detail": "the coding model didn't download"})); return 1
        path, src, lang, note = code.write(a.text)
        print(json.dumps({"result": "ok", "path": path, "lang": lang, "lines": len(src.splitlines()),
                          "seconds": round(time.monotonic() - t0, 1)}))
        return 0
    img = creator.ImageMaker(cfg, show=show)
    ok, kind = safety.check_prompt(a.text)
    if not ok:
        print(json.dumps({"result": "refused", "detail": kind})); return 1
    if not img.installed():
        img.install(progress=lambda label, pct: print(f"{label} {pct}%", flush=True) if pct % 25 == 0 else None)
    res, detail = img.generate(a.text)
    print(json.dumps({"result": res, "detail": detail, **(img.last or {}), "total_seconds": round(time.monotonic() - t0, 1)}))
    return 0 if res == "ok" else 1

def workspace_cmd(a):
    from . import workspace
    vis, how = workspace.control(a.what, run=None if a.dry_run else Runner())
    print(json.dumps({"visible": vis, "how": how}))
    return 1 if how == "failed" else 0

def apps_install(run, a):
    import os
    from . import apps
    ids = apps.load_list(a.list or os.path.join(apps.SHARE, "flatpaks.list"))
    res = apps.install_flatpaks(run, ids, notify=lambda m: print(m, flush=True))
    failed = [k for k, v in res.items() if v == "failed"]
    print(json.dumps(res, indent=1))
    if a.first_boot and not failed:
        os.makedirs(apps.STATE, exist_ok=True)
        open(os.path.join(apps.STATE, "apps.done"), "w").write("ok\n")
    return 1 if failed else 0

def games(run, a):
    """Reads (never writes) the Windows Steam library and queues the Linux-compatible games."""
    import os
    from . import apps
    mounts = dict(m.split("=", 1) for m in a.mount)
    mounts = {k.upper(): v for k, v in mounts.items()}
    if not mounts:
        for dev, label, mp in apps.ntfs_partitions(run):
            if not mp:
                rc, out = run(["udisksctl", "mount", "-b", dev, "-o", "ro"], timeout=60)   # read-only, always
                mp = out.rsplit(" at ", 1)[-1].strip().rstrip(".") if rc == 0 and " at " in out else None
            if mp:
                letter = "C" if apps._ci(mp, "Windows", "System32") else chr(ord("D") + len([k for k in mounts if k != "C"]))
                mounts.setdefault(letter, mp)
    if not mounts:
        print("No Windows drive found. Mount it read-only and pass --mount C=/path.")
        return 2
    found = apps.find_windows_steam(mounts)
    target = a.steamapps or apps.linux_steamapps()
    install, skip, tweaks = apps.plan_restore(found, apps.load_compat(), target)
    if a.what == "list" or getattr(run, "calls", None) is not None:      # list, or --dry-run
        print(json.dumps({"install": install, "stays_on_windows": skip, "tweaks": tweaks}, indent=1))
        return 0
    rc, _ = run(["pgrep", "-x", "steam"], timeout=5)
    if rc == 0:
        print("Close Steam first (Steam > Exit), then run this again.")
        return 3
    written = apps.queue_downloads(install, target)
    print(f"Queued {len(written)} games; open Steam and they start downloading.")
    for s in skip:
        print(f"Stays on Windows: {s['name']} ({s['why']})")
    for t in tweaks:
        print(f"Tip for {t['name']}: Properties > Launch options: {t['launch_options']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())

def link(a, ask_key=None):
    """Link to Tab: copy only the Tab link (mailbox, relay, reply topic and key) from Windows JARVIS's config.json,
    or fetch it with a one-time code from Tab. Never changes anything on the Windows drive."""
    import os, subprocess
    from . import tablink
    cfg_path = a.config or os.path.expanduser("~/.config/jarvis/config.json")
    done = os.path.expanduser("~/.local/state/jarvis/link.done")
    if a.code:
        from . import linkcode
        try:
            settings = linkcode.fetch(a.code)
        except ValueError:
            print("That isn't a JARVIS link code (it looks like 7KQ2-M9XD-R4TB)."); return 1
        except OSError as e:
            print(f"Couldn't reach the link service ({e}). Check the internet connection and try again."); return 1
        if not settings:
            print("That code didn't work: it's wrong or more than an hour old. Ask Tab for a new one."); return 1
        cfg = tablink.merge_link(settings, cfg_path)
        return _linked(cfg, "a link code from Tab", cfg_path, done)
    root = a.windows_root or tablink.mount_windows_ro(interactive=not a.first_boot)
    found = tablink.find_windows_configs(root) if root else []
    opened = False
    try:
        if not found and not a.windows_root and a.bitlocker:
            parts = tablink.bitlocker_partitions()
            if not parts:
                print("No BitLocker-encrypted drive found on this PC."); return 1
            import getpass
            key = tablink.normalize_recovery_key((ask_key or getpass.getpass)(
                "Windows recovery key (48 digits, from aka.ms/myrecoverykey; it stays on this PC): "))
            if not key:
                print("That isn't a recovery key: it's 48 digits in 8 groups of 6."); return 1
            root = tablink.unlock_windows_ro(parts[0], key); key = None
            opened = bool(root)
            if not root:
                print("Couldn't open the Windows drive with that key (wrong key, or the password prompt was cancelled)."); return 1
            found = tablink.find_windows_configs(root)
        if not found:
            why = f" on {root}" if root else " (no Windows drive could be mounted)"
            hint = "Link later with: jarvisctl link --from-windows"
            if not root and tablink.bitlocker_present():
                why = " (the Windows drive is BitLocker-encrypted, so Linux can't read it without its key)"
                hint = ("Link with ONE of: jarvisctl link --from-windows --bitlocker (needs the 48-digit recovery key), "
                        "or ask Tab for a link code and run: jarvisctl link --code XXXX-XXXX-XXXX")
            print("No Windows JARVIS found" + why + ". " + hint)
            if a.first_boot and "BitLocker" in why:   # the first-login service has no terminal: tell the person on screen
                subprocess.run(["notify-send", "-a", "J.A.R.V.I.S.", "-i", "dialog-password", "Link JARVIS to Tab",
                                "Windows is encrypted, so I can't copy the Tab link by myself. In Konsole run "
                                "'jarvisctl link --from-windows --bitlocker' (needs your recovery key), "
                                "or text Tab for a link code and run 'jarvisctl link --code ...'."], capture_output=True)
            return 0 if a.first_boot else 1
        cfg = tablink.import_link(found[0], cfg_path)
        return _linked(cfg, found[0], cfg_path, done)
    finally:
        if opened:
            tablink.lock_windows()

def _linked(cfg, source, cfg_path, done):
    import os, subprocess
    to = cfg.get("forwarding", {}).get("to", "")
    print(f"Linked to Tab ({to.split('@')[0][:2]}…@{to.split('@')[-1]}) from {source}; settings in {cfg_path}")
    os.makedirs(os.path.dirname(done), exist_ok=True); open(done, "w").write(source + "\n")
    subprocess.run(["systemctl", "--user", "try-restart", "jarvisd.service"], capture_output=True)
    return 0
