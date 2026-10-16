"""Over-the-air updates for JARVIS OS.

Lane 1, the OS image (bootc): every build of github.com/<org>/jarvis-os is pushed to
ghcr.io/<org>/jarvis-os:<channel> and signed with cosign. `bootc upgrade` stages the new image for the next
boot atomically; the running system is never modified in place, and `bootc rollback` returns to the previous
image (also listed in the boot menu). Channels: stable (default), beta (Tab's daily builds), and a pinned digest.

Lane 2, the JARVIS app (minutes, no reboot): jarvisd checks a signed manifest (Ed25519) and swaps
~/.local/share/jarvis/app/<version> atomically. Only a manifest signed with the JARVIS OS release key applies.
"""
from __future__ import annotations
import json

# bootc needs root; jarvisd runs as the person. Every bootc call goes through the fixed root helper via pkexec
# (polkit action os.jarvis.os-update; the image's polkit rule lets the admin account run it without a prompt).
HELPER = ["pkexec", "/usr/libexec/jarvis-os/os-update"]

class Bootc:
    def __init__(self, run):
        self.run = run

    def status(self):
        code, out = self.run(HELPER + ["status"], timeout=60)
        if code != 0:
            raise RuntimeError(out.strip()[:300])
        st = json.loads(out)
        s = st.get("status", {})
        def img(slot):
            x = s.get(slot) or {}
            i = (x.get("image") or {})
            return {"image": ((i.get("image") or {}).get("image")), "version": i.get("version"),
                    "digest": i.get("imageDigest"), "timestamp": i.get("timestamp")} if x else None
        return {"booted": img("booted"), "staged": img("staged"), "rollback": img("rollback")}

    def check(self):
        code, out = self.run(HELPER + ["check"], timeout=180)
        if code != 0:
            raise RuntimeError(out.strip()[:300])
        if "No changes" in out or "No update available" in out:
            return None
        return out.strip()

    def stage(self):
        """Download + stage the update for next boot. Never reboots by itself."""
        code, out = self.run(HELPER + ["stage"], timeout=3600)
        return code == 0, out.strip()[-400:]

    def rollback(self):
        code, out = self.run(HELPER + ["rollback"], timeout=180)
        return code == 0, out.strip()[-400:]

    def switch_channel(self, ref):
        if not ref.startswith("ghcr.io/") or any(c in ref for c in " ;|&$`"):
            raise ValueError("refusing image reference outside ghcr.io")
        channel = ref.rsplit(":", 1)[-1] if ":" in ref.split("/")[-1] else ""
        if channel not in ("stable", "beta"):
            raise ValueError("only the stable and beta channels of this image")
        code, out = self.run(HELPER + ["switch", channel], timeout=3600)
        return code == 0, out.strip()[-400:]
