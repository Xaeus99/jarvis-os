#!/usr/bin/env python3
"""Splits the installer ISO into parts under GitHub's 2 GiB release-asset limit and writes the download/join
scripts (Windows .bat, Linux/macOS .sh), SHA256SUMS and the release notes.

    make_release.py ISO OUTDIR BASEURL CHANNEL IMAGE_REF

Self-checks: every part < 2 GiB, the parts rejoin to the exact ISO, and the .sh script joins + verifies them."""
import hashlib, os, subprocess, sys
from pathlib import Path

PART = 1900 * 1024 * 1024          # 1,992,294,400 bytes < 2 GiB (2,147,483,648)
iso, out, base, channel, image_ref = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3].rstrip("/"), sys.argv[4], sys.argv[5]
here = Path(__file__).resolve().parent
out.mkdir(parents=True, exist_ok=True)
name = iso.name

whole, parts = hashlib.sha256(), []
with iso.open("rb") as f:
    i = 0
    while True:
        p = out / f"{name}.part{i}"
        h, n = hashlib.sha256(), 0
        with p.open("wb") as o:
            while n < PART:
                b = f.read(min(16 << 20, PART - n))
                if not b: break
                o.write(b); h.update(b); whole.update(b); n += len(b)
        if n == 0: p.unlink(); break
        assert n < 2 ** 31, (p, n)
        parts.append((p.name, n, h.hexdigest())); i += 1
sha = whole.hexdigest(); size = sum(n for _, n, _ in parts)
assert size == iso.stat().st_size

def fill(src, dst, crlf, values):
    t = (here / src).read_text()
    for k, v in values.items(): t = t.replace(k, v)
    assert "@" + "ISO@" not in t and "@SHA@" not in t and "@GETS@" not in t, dst
    t = t.replace("\r\n", "\n")
    (out / dst).write_bytes((t.replace("\n", "\r\n") if crlf else t).encode())

values = {
    "@ISO@": name, "@SHA@": sha, "@BASEURL@": base, "@NPARTS@": str(len(parts)),
    "@GETS@": "\n".join(f'call :get "{p}" {n} || goto :fail' for p, n, _ in parts),
    "@JOIN@": "+".join(f'"{p}"' for p, _, _ in parts),
    "@LIST@": "\n".join(f"{p} {n}" for p, n, _ in parts),
    "@CATPARTS@": " ".join(p for p, _, _ in parts),
}
fill("get-jarvis-os.bat.in", "get-jarvis-os.bat", True, values)
fill("get-jarvis-os.sh.in", "get-jarvis-os.sh", False, values)
os.chmod(out / "get-jarvis-os.sh", 0o755)

def fsha(p): 
    h = hashlib.sha256(); h.update((out / p).read_bytes()); return h.hexdigest()
sums = [f"{h}  {p}" for p, _, h in parts] + [f"{fsha(s)}  {s}" for s in ("get-jarvis-os.bat", "get-jarvis-os.sh")] + [f"{sha}  {name}"]
(out / "SHA256SUMS").write_text("\n".join(sums) + "\n")

# end-to-end check of the .sh joiner (parts present, so it downloads nothing), then drop the rejoined copy
r = subprocess.run(["sh", "./get-jarvis-os.sh"], cwd=out, capture_output=True, text=True)
print(r.stdout, r.stderr)
assert r.returncode == 0 and "OK:" in r.stdout, "join script failed"
(out / name).unlink()

gib = size / 2 ** 30
state = "stable" if channel == "stable" else "beta (testing)"
repo = os.environ.get("GITHUB_REPOSITORY", "OWNER/jarvis-os")
join_cmd = "+".join(p for p, _, _ in parts)
notes = f"""**JARVIS OS installer, {state} channel.** One file, `{name}` ({gib:.1f} GiB, SHA-256 `{sha}`), split into {len(parts)} parts because GitHub caps a release file at 2 GiB. No GitHub account is needed to download.

### Windows: the easy way
1. Download **get-jarvis-os.bat** (below, under *Assets*) into a folder with about 15 GB free. Downloads is fine.
2. If the browser warns about the file, choose **Keep**. Double-click it. If Windows says *"Windows protected your PC"*, click **More info > Run anyway**. It's a plain text script; right-click > *Edit* shows every line.
3. It downloads the {len(parts)} parts, joins them into `{name}` and checks the checksum. It ends with **OK**. Then you can delete the `.part` files.

### Windows: by hand
Download all {len(parts)} `.part` files into one folder, open Command Prompt in that folder and run:
```
copy /b {join_cmd} {name}
```
Check it in PowerShell: `Get-FileHash {name}` must print `{sha.upper()}`.

### Linux / macOS
`sh get-jarvis-os.sh` downloads, joins and checks. By hand: `cat {values['@CATPARTS@']} > {name}`, then compare `sha256sum {name}` with SHA256SUMS.

### Then
Write the ISO to a USB stick with Fedora Media Writer (*Select .iso file*) or Rufus (DD mode). Then follow [the install-day checklist](https://github.com/{repo}/blob/main/docs/INSTALL_DAY.md).
Installed systems update themselves from `{image_ref}`.

Tested in virtual machines only: the ISO boots to the installer, and an unattended dual-boot install leaves a dummy Windows byte-identical. **Not yet tested on real hardware.**
"""
(out / "NOTES.md").write_text(notes)
for p in sorted(out.iterdir()): print(p.name, p.stat().st_size)
print(f"ISO_SHA256={sha} PARTS={len(parts)}")
