"""jarvisctl: talk to JARVIS from a terminal or a KDE shortcut.
  jarvisctl say "set a timer for 5 minutes"   run one phrase through the brain (prints the spoken reply)
  jarvisctl takeback                          the kill switch (bound to Meta+Shift+Esc)
  jarvisctl status                            remote-control state + OS image versions
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
    a = ap.parse_args(argv)
    run = DryRun() if a.dry_run else Runner()
    b = LinuxBrain(run=run, ask=(lambda q: (print("JARVIS asks:", q), "yes")[1]) if a.yes else (lambda q: (print("JARVIS asks:", q), "")[1]))
    if a.cmd == "say":
        print("JARVIS:", b.handle(a.text))
    elif a.cmd == "takeback":
        n = b.remote.takeback(by="jarvisctl"); print(f"control taken back ({n} queued actions dropped)")
    elif a.cmd == "status":
        print(json.dumps({"remote": b.remote.st}, indent=1))
    return 0

if __name__ == "__main__":
    sys.exit(main())
