"""JARVIS OS graphics check: is the NVIDIA driver really driving the RTX card, and if not, why and what to do.

Facts only, each read from where Linux keeps it:
  /sys/bus/pci/devices/*            which display devices exist, their vendor/device id and the driver bound to each
  /sys/module/nvidia(_drm)          the NVIDIA kernel module, its version, and whether modesetting is on
  /proc/cmdline                     whether this boot is the "JARVIS OS - safe graphics" entry (nomodeset, NVIDIA kept off)
  /sys/firmware/efi/efivars         whether Secure Boot is on
  mokutil --list-new                whether the NVIDIA signing key is queued for enrollment (the blue screen at restart)
  journalctl -k -b                  the kernel's own words when it refused or failed to start the driver
  nvidia-smi                        the card's name and driver version, when the driver answers

Runs at every login (jarvis-gpu-check.service, a notification only when something is wrong), from the voice
("check my graphics card") and as `jarvisctl gpu check [--json]`.
"""
from __future__ import annotations
import glob, json, os, re, subprocess

NVIDIA = "0x10de"
SB_VAR = "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
ENROLL_CMD = ["ujust", "enroll-secure-boot-key"]          # Bazzite's own recipe: queues /etc/pki/akmods/certs/akmods-ublue.der
STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "jarvis", "gpu-check.json")

# RTX 50 (Blackwell, GB20x) PCI device ids sit in 0x2B80-0x2FFF; the RTX 5060 desktop card is 0x2D05 (5060 Ti: 0x2D04)
NAMES = {0x2D05: "GeForce RTX 5060", 0x2D04: "GeForce RTX 5060 Ti", 0x2C05: "GeForce RTX 5070 Ti", 0x2F04: "GeForce RTX 5070",
         0x2C02: "GeForce RTX 5080", 0x2B85: "GeForce RTX 5090"}

def is_rtx50(devid):
    return 0x2B80 <= devid <= 0x2FFF

KEY_REJECTED = re.compile(r"(Loading of (?:unsigned )?module.*(?:rejected|unavailable key)|Key was rejected by service|"
                          r"module verification failed.*nvidia|nvidia.*(?:Key was rejected|Required key not available))", re.I)
NOT_SUPPORTED = re.compile(r"NVRM:.*(?:not supported by|is not supported|unsupported)", re.I)
INIT_FAILED = re.compile(r"NVRM:.*(?:RmInitAdapter failed|GPU .* has fallen off the bus|failed to (?:initialize|load))", re.I)

def _read(path, default=""):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return default

def pci_displays(sysfs="/sys"):
    """Every display-class PCI device: [{slot, vendor, device, driver}] (driver None when nothing is bound)."""
    out = []
    for d in sorted(glob.glob(os.path.join(sysfs, "bus/pci/devices/*"))):
        cls = _read(os.path.join(d, "class")).strip()
        if not cls.startswith("0x03"):
            continue
        drv = os.path.join(d, "driver")
        out.append({"slot": os.path.basename(d), "vendor": _read(os.path.join(d, "vendor")).strip().lower(),
                    "device": int(_read(os.path.join(d, "device"), "0x0").strip() or "0x0", 16),
                    "driver": os.path.basename(os.path.realpath(drv)) if os.path.exists(drv) else None})
    return out

def secure_boot(sysfs="/sys"):
    """True / False, or None when this isn't a UEFI boot (or the variable can't be read)."""
    p = os.path.join(sysfs, "firmware/efi/efivars", SB_VAR)
    try:
        with open(p, "rb") as f:
            b = f.read()
        return bool(b) and b[-1] == 1
    except OSError:
        return None if not os.path.isdir(os.path.join(sysfs, "firmware/efi")) else False

def _run(run, argv, timeout=10):
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)

def gather(sysfs="/sys", proc="/proc", run=subprocess.run):
    """Everything the verdict needs, as plain data (tests hand in a fake /sys and /proc and a fake run)."""
    f = {"gpus": pci_displays(sysfs), "secure_boot": secure_boot(sysfs),
         "cmdline": _read(os.path.join(proc, "cmdline")).strip(),
         "module": os.path.isdir(os.path.join(sysfs, "module/nvidia")),
         "driver_version": _read(os.path.join(sysfs, "module/nvidia/version")).strip() or None,
         "modeset": _read(os.path.join(sysfs, "module/nvidia_drm/parameters/modeset")).strip() or None}
    f["nvidia"] = [g for g in f["gpus"] if g["vendor"] == NVIDIA]
    f["smi"] = None
    if f["nvidia"] and f["module"]:
        rc, out = _run(run, ["nvidia-smi", "--query-gpu=name,driver_version,temperature.gpu", "--format=csv,noheader"], 15)
        if rc == 0 and out.strip():
            f["smi"] = out.strip().splitlines()[0]
    f["mok_pending"] = False
    f["kernel"] = []
    if f["nvidia"] and not _bound(f):
        rc, out = _run(run, ["mokutil", "--list-new"])
        f["mok_pending"] = rc == 0 and any(w in out.lower() for w in ("ublue", "akmods", "subject:"))
        rc, out = _run(run, ["journalctl", "-k", "-b", "-o", "cat", "--no-pager", "-g", "nvidia|NVRM|Lockdown|module verification|Key was rejected"], 20)
        f["kernel"] = [l for l in out.splitlines() if l.strip()][-12:] if rc == 0 else []
    return f

def _bound(f):
    return any(g["driver"] == "nvidia" for g in f["nvidia"])

def card_name(f):
    if f.get("smi"):
        return f["smi"].split(",")[0].strip()
    for g in f.get("nvidia", []):
        return NAMES.get(g["device"], "NVIDIA RTX 50-series card" if is_rtx50(g["device"]) else f"NVIDIA card {g['device']:04x}")
    return "graphics card"

def verdict(f):
    """{code, ok, headline, fix} in plain words. code is one of:
    ok, no_nvidia, safe_graphics, secure_boot_key, unsupported, nouveau, driver_failed, modeset_off."""
    name = card_name(f)
    cmd = f.get("cmdline", "")
    safe = "nomodeset" in cmd.split() or re.search(r"(?:modprobe|rd\.driver)\.blacklist=\S*nvidia", cmd) is not None
    kern = "\n".join(f.get("kernel", []))
    if not f.get("nvidia"):
        return {"code": "no_nvidia", "ok": True, "headline": "No NVIDIA card in this computer, so there's no NVIDIA driver to check.", "fix": ""}
    if _bound(f):
        if f.get("modeset") not in (None, "Y", "y", "1"):
            return {"code": "modeset_off", "ok": False, "headline": f"The {name} is on the NVIDIA driver, but kernel modesetting is off, so the desktop may tear or fall back to a low resolution.",
                    "fix": "Restart and pick the normal JARVIS OS entry (not safe graphics). If it stays off, say \u201croll back the update\u201d."}
        drv = f.get("driver_version")
        return {"code": "ok", "ok": True, "headline": f"The {name} is running on the NVIDIA driver{(' ' + drv) if drv else ''}, modesetting on." +
                (f" Secure Boot is on and the driver's key is trusted." if f.get("secure_boot") else ""), "fix": "",
                "short": f"{name.replace('NVIDIA ', '')} on the NVIDIA driver{(' ' + drv) if drv else ''}" + (", Secure Boot on." if f.get("secure_boot") else ".")}
    if safe:
        return {"code": "safe_graphics", "ok": True, "headline": f"This start is the safe graphics entry, so the NVIDIA driver is kept off on purpose and the {name} runs a basic picture.",
                "fix": "Restart and pick \u201cJARVIS OS\u201d at the top of the boot menu for full graphics."}
    if any(g["driver"] == "nouveau" for g in f["nvidia"]):
        return {"code": "nouveau", "ok": False, "headline": f"The {name} is on the basic open-source driver instead of NVIDIA's, so games will run slowly.",
                "fix": "Restart once. If it's still the basic driver, say \u201croll back the update\u201d and tell Tab."}
    if f.get("secure_boot") and (KEY_REJECTED.search(kern) or f.get("mok_pending") or not f.get("module")):
        if f.get("mok_pending"):
            fix = ("The key is already queued. Restart: on the blue screen pick Enroll MOK > Continue > Yes, type universalblue, then Reboot.")
        else:
            fix = ("Say \u201cfix the graphics driver\u201d (or run: ujust enroll-secure-boot-key, password universalblue), restart, and on the blue "
                   "screen pick Enroll MOK > Continue > Yes, type universalblue, then Reboot. Or turn Secure Boot off in the BIOS.")
        return {"code": "secure_boot_key", "ok": False, "headline": f"Secure Boot stopped the NVIDIA driver: its signing key isn't enrolled on this PC yet, so the {name} is on a basic picture.", "fix": fix}
    if NOT_SUPPORTED.search(kern):
        return {"code": "unsupported", "ok": False, "headline": f"The installed NVIDIA driver says it doesn't support the {name}.",
                "fix": "Pick the safe graphics entry for now and say \u201ccheck for updates\u201d; a newer image carries a newer driver."}
    return {"code": "driver_failed", "ok": False, "headline": f"The NVIDIA driver didn't start for the {name}" + (" (it loaded but couldn't take the card)." if f.get("module") else "."),
            "fix": "Restart once. If it happens again, say \u201croll back the update\u201d, or pick the safe graphics entry and tell Tab what this check says."}

def check(save=True, **kw):
    f = gather(**kw)
    v = verdict(f)
    v["facts"] = {k: f[k] for k in ("secure_boot", "module", "driver_version", "modeset", "smi", "mok_pending")}
    v["facts"]["gpus"] = [dict(g, device=f"0x{g['device']:04x}") for g in f["gpus"]]
    v["facts"]["kernel"] = f["kernel"]
    if save:
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            with open(STATE, "w") as fh:
                json.dump(v, fh, indent=1)
        except OSError:
            pass
    return v

def spoken(v, sir="sir"):
    s = f"Graphics check, {sir}. " + v["headline"]
    if v.get("fix"):
        s += " " + v["fix"]
    return s

def notify(v, run=subprocess.run):
    """A desktop notification, only when something needs him."""
    if v.get("ok"):
        return False
    _run(run, ["notify-send", "-a", "J.A.R.V.I.S.", "-u", "critical", "-i", "video-display", "Graphics check", v["headline"] + "\n" + v.get("fix", "")])
    return True

class _Done:
    def __init__(self, code, out): self.returncode, self.stdout, self.stderr = code, out, ""

def runner_adapter(runner):
    """JARVIS's Runner (argv, timeout) -> (code, output) seen as subprocess.run, for gather()/setupcheck."""
    def run(argv, capture_output=True, text=True, timeout=15, **kw):
        code, out = runner(argv, timeout=timeout)
        return _Done(code, out or "")
    return run
