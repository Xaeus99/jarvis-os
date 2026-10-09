"""Switching between JARVIS OS and Windows without ever modifying Windows.

Linux side ("Jarvis, switch to Windows"): set the firmware's one-time BootNext to the Windows Boot Manager
entry with efibootmgr, then reboot. The permanent boot order is untouched, so the next boot after that comes
back to the JARVIS menu. efibootmgr needs root, so the image ships a tiny helper
(/usr/libexec/jarvis-os/bootnext-windows) and a polkit rule letting the logged-in user run only that helper.

Windows side ("Jarvis, switch to JARVIS OS", JARVIS v5.x): `bcdedit /set {fwbootmgr} bootsequence {GUID}`
does the same one-time trick from Windows; see windows/Switch-To-JarvisOS.ps1. The parser for
`bcdedit /enum firmware` lives here too so it is tested on Linux.
"""
from __future__ import annotations
import re

WINDOWS_RE = re.compile(r"windows boot manager", re.I)
JARVIS_RE = re.compile(r"jarvis|bazzite|fedora", re.I)

def parse_efibootmgr(text):
    """Return {'current': '0001', 'order': [...], 'entries': {'0000': 'Windows Boot Manager', ...}}."""
    cur = re.search(r"^BootCurrent:\s*([0-9A-Fa-f]{4})", text, re.M)
    order = re.search(r"^BootOrder:\s*([0-9A-Fa-f,]+)", text, re.M)
    entries = {}
    for m in re.finditer(r"^Boot([0-9A-Fa-f]{4})(\*?)\s+(.+?)(?:\t|\s{2,}|\s+(?:HD|PciRoot|VenHw|BBS)\(|$)", text, re.M):
        entries[m.group(1).upper()] = m.group(3).strip()
    return {"current": cur.group(1).upper() if cur else None,
            "order": [x.upper() for x in order.group(1).split(",")] if order else [],
            "entries": entries}

def find_entry(parsed, pattern):
    for num, label in parsed["entries"].items():
        if pattern.search(label):
            return num, label
    return None

def windows_bootnext_argv(efibootmgr_text):
    """The exact commands to boot Windows once. Raises LookupError if no Windows entry exists."""
    p = parse_efibootmgr(efibootmgr_text)
    hit = find_entry(p, WINDOWS_RE)
    if not hit:
        raise LookupError("No Windows Boot Manager entry in the firmware boot list")
    return [["efibootmgr", "--bootnext", hit[0]], ["systemctl", "reboot"]]

def parse_bcdedit_firmware(text):
    """Parse `bcdedit /enum firmware` (English) into a list of {'id','description'} blocks."""
    out = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        ident = re.search(r"^identifier\s+(\{[^}]+\})", block, re.M | re.I)
        desc = re.search(r"^description\s+(.+)$", block, re.M | re.I)
        if ident:
            out.append({"id": ident.group(1), "description": desc.group(1).strip() if desc else ""})
    return out

def jarvis_bootsequence_argv(bcdedit_text):
    """Commands (run elevated on Windows) to boot JARVIS OS once."""
    for e in parse_bcdedit_firmware(bcdedit_text):
        if e["id"].lower() in ("{fwbootmgr}", "{bootmgr}"):
            continue
        if JARVIS_RE.search(e["description"]):
            return [["bcdedit", "/set", "{fwbootmgr}", "bootsequence", e["id"]], ["shutdown", "/r", "/t", "5"]]
    raise LookupError("No JARVIS OS entry in the firmware boot list")
