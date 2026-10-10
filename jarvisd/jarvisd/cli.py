"""jarvisctl: talk to JARVIS from a terminal or a KDE shortcut.
  jarvisctl say "set a timer for 5 minutes"   run one phrase through the brain (prints the spoken reply)
  jarvisctl takeback                          the kill switch (bound to Meta+Shift+Esc)
  jarvisctl status                            remote-control state + OS image versions
  jarvisctl apps install                      install the JARVIS OS app list from Flathub (first boot does this once)
  jarvisctl games restore [--mount C=/path]   queue this PC's Windows Steam games for download on Linux
  jarvisctl link --from-windows               link to the same Tab as Windows JARVIS (reads the Windows drive read-only)
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
    p = sub.add_parser("link"); p.add_argument("--from-windows", action="store_true", required=True)
    p.add_argument("--windows-root", default=None, help="an already-mounted Windows drive (default: mount it read-only)")
    p.add_argument("--config", default=None); p.add_argument("--first-boot", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "link":
        return link(a)
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

def link(a):
    """Copy only the Tab link (mailbox, relay, reply topic and key) from Windows JARVIS's config.json."""
    import os, subprocess
    from . import tablink
    cfg_path = a.config or os.path.expanduser("~/.config/jarvis/config.json")
    done = os.path.expanduser("~/.local/state/jarvis/link.done")
    root = a.windows_root or tablink.mount_windows_ro(interactive=not a.first_boot)
    found = tablink.find_windows_configs(root) if root else []
    if not found:
        why = f" on {root}" if root else " (no Windows drive could be mounted)"
        if not root and tablink.bitlocker_present():
            why = " (the Windows drive is BitLocker-encrypted, so Linux can't read it)"
        print("No Windows JARVIS found" + why + ". Link later with: jarvisctl link --from-windows")
        return 0 if a.first_boot else 1
    cfg = tablink.import_link(found[0], cfg_path)
    to = cfg.get("forwarding", {}).get("to", "")
    print(f"Linked to Tab ({to.split('@')[0][:2]}…@{to.split('@')[-1]}) from {found[0]}; settings in {cfg_path}")
    os.makedirs(os.path.dirname(done), exist_ok=True); open(done, "w").write(found[0] + "\n")
    subprocess.run(["systemctl", "--user", "try-restart", "jarvisd.service"], capture_output=True)
    return 0
