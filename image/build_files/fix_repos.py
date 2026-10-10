#!/usr/bin/env python3
"""Remove any dnf repo (enabled or not) whose GPG key file (file://...) is missing from the image.

Bazzite ships a 'terra-mesa' repo pointing at /etc/pki/rpm-gpg/RPM-GPG-KEY-terra<ver>-mesa, which is not
in the image. Package installs from the image never touch it (updates come as whole signed images), but
bootc-image-builder depsolves the installer from these repos and stops on the missing key. Disabling a
repo that cannot be verified anyway changes nothing else. The installer build reads disabled repos too, so the
section is removed rather than switched off.

Also: a key file that is a symlink is replaced by a plain copy of its target. bootc-image-builder reads the
image's key files from outside the image, where an absolute symlink points at the builder's own (empty) /usr,
so a key that exists in the image still "can't be read".

And: an enabled repo with repo_gpgcheck=1 (signed metadata, terra-mesa) is switched off, here and in dnf's
repos.override.d. The installer builder checks repo metadata with keys from its own container, not the image's,
so such a repo always fails there. JARVIS OS updates as whole signed images, so nothing on the installed system
uses that repo; `rpm-ostree install` of a terra package would need it switched back on.
"""
import configparser, glob, os, platform, re

ver = "0"
for line in open("/etc/os-release"):
    if line.startswith("VERSION_ID="):
        ver = line.split("=", 1)[1].strip().strip('"')
subs = {"releasever": ver, "releasever_major": ver.split(".")[0], "basearch": platform.machine(), "arch": platform.machine()}

def expand(s):
    return re.sub(r"\$\{?(\w+)\}?", lambda m: subs.get(m.group(1), m.group(0)), s)

switched_off = []
for f in sorted(glob.glob("/etc/yum.repos.d/*.repo")):
    c = configparser.RawConfigParser(strict=False)
    c.optionxform = str
    c.read(f)
    changed = False
    for s in c.sections():
        keys = re.split(r"[\s,]+", c.get(s, "gpgkey", fallback="").strip())
        missing = [k for k in keys if k.startswith("file://") and not os.path.exists(expand(k[7:]))]
        print(f"fix_repos: [{s}] in {f}: enabled={c.get(s, 'enabled', fallback='1').strip()}"
              + (f", MISSING key {', '.join(missing)} -> removed" if missing else ""))
        for k in keys:
            p = expand(k[7:]) if k.startswith("file://") else None
            if p and os.path.islink(p) and os.path.exists(p):
                real = os.path.realpath(p)
                data = open(real, "rb").read()
                os.remove(p)
                with open(p, "wb") as fh:
                    fh.write(data)
                print(f"fix_repos: key {p} was a symlink to {real}; now a plain file")
        if missing:
            c.remove_section(s)
            changed = True
        elif c.get(s, "enabled", fallback="1").strip() in ("1", "true", "True") and \
                c.get(s, "repo_gpgcheck", fallback="0").strip() in ("1", "true", "True"):
            c.set(s, "enabled", "0")
            switched_off.append(s)
            changed = True
            print(f"fix_repos: [{s}] has signed metadata (repo_gpgcheck=1) -> enabled=0")
    if changed:
        if c.sections():
            with open(f, "w") as fh:
                c.write(fh)
        else:
            os.remove(f)

# dnf5 keeps `config-manager setopt` results here, and they win over the .repo files
for f in sorted(glob.glob("/etc/dnf/repos.override.d/*.repo")):
    c = configparser.RawConfigParser(strict=False)
    c.optionxform = str
    c.read(f)
    hit = [s for s in c.sections() if s in switched_off]
    for s in hit:
        c.set(s, "enabled", "0")
        print(f"fix_repos: [{s}] enabled=0 in {f}")
    if hit:
        with open(f, "w") as fh:
            c.write(fh)
