"""Quest headset pairing on JARVIS OS (stub for the VR bridge).

The JARVIS VR app (a WebXR app for the Meta Quest browser) pairs with JARVIS on the PC using a 6-digit code that
JARVIS shows and says, over the home network only. The bridge itself ships first in Windows JARVIS 7.1; this module
is the JARVIS OS side's pairing groundwork so the bridge can drop in: the code, its expiry, the LAN address the
headset would connect to, and the honest spoken line until then.
"""
from __future__ import annotations
import ipaddress, json, os, secrets, socket, time

STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "jarvis", "vr-pair.json")
CODE_TTL_S = 600
BRIDGE_READY = False          # flips when the VR bridge service lands in the image

def make_code(rng=secrets.randbelow):
    return f"{rng(1_000_000):06d}"

def lan_ip(connect=None):
    """The private IPv4 this PC uses on the home network (no packet is sent), or None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            (connect or s.connect)(("192.168.0.1", 9))
            ip = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None
    try:
        return ip if ipaddress.ip_address(ip).is_private and not ipaddress.ip_address(ip).is_loopback else None
    except ValueError:
        return None

def begin_pairing(now=None, path=STATE, ip=None, rng=secrets.randbelow):
    """A fresh code (valid 10 minutes), saved for the bridge to check. Returns the record."""
    rec = {"code": make_code(rng), "expires": int((now or time.time()) + CODE_TTL_S), "lan_ip": ip if ip is not None else lan_ip()}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(rec, f)
    os.chmod(path, 0o600)
    return rec

def check_code(code, now=None, path=STATE):
    """True once for a matching, unexpired code (it's used up on success)."""
    try:
        rec = json.load(open(path))
    except (OSError, ValueError):
        return False
    if (now or time.time()) > rec.get("expires", 0) or not secrets.compare_digest(str(code), str(rec.get("code"))):
        return False
    os.remove(path)
    return True

def pair_line(sir="sir"):
    if BRIDGE_READY:
        rec = begin_pairing()
        digits = " ".join(rec["code"])
        return f"Pairing code {digits}, {sir}. Open JARVIS VR on the Quest and type it in; it's good for ten minutes."
    return (f"The headset bridge isn't on JARVIS OS yet, {sir}; it arrives in a coming update. Until then the JARVIS VR app "
            f"pairs with JARVIS on Windows.")
