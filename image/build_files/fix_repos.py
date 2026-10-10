#!/usr/bin/env python3
"""Disable any enabled dnf repo whose GPG key file (file://...) is missing from the image.

Bazzite ships a 'terra-mesa' repo pointing at /etc/pki/rpm-gpg/RPM-GPG-KEY-terra<ver>-mesa, which is not
in the image. Package installs from the image never touch it (updates come as whole signed images), but
bootc-image-builder depsolves the installer from these repos and stops on the missing key. Disabling a
repo that cannot be verified anyway changes nothing else.
"""
import configparser, glob, os, platform, re

ver = "0"
for line in open("/etc/os-release"):
    if line.startswith("VERSION_ID="):
        ver = line.split("=", 1)[1].strip().strip('"')
subs = {"releasever": ver, "releasever_major": ver.split(".")[0], "basearch": platform.machine(), "arch": platform.machine()}

def expand(s):
    return re.sub(r"\$\{?(\w+)\}?", lambda m: subs.get(m.group(1), m.group(0)), s)

for f in sorted(glob.glob("/etc/yum.repos.d/*.repo")):
    c = configparser.RawConfigParser(strict=False)
    c.optionxform = str
    c.read(f)
    changed = False
    for s in c.sections():
        if c.get(s, "enabled", fallback="1").strip() not in ("1", "true", "True"):
            continue
        keys = re.split(r"[\s,]+", c.get(s, "gpgkey", fallback="").strip())
        missing = [k for k in keys if k.startswith("file://") and not os.path.exists(expand(k[7:]))]
        if missing:
            c.set(s, "enabled", "0")
            changed = True
            print(f"fix_repos: disabled [{s}] in {f} (missing {', '.join(missing)})")
    if changed:
        with open(f, "w") as fh:
            c.write(fh)
