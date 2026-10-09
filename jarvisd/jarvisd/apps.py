"""JARVIS OS apps: first-boot Flatpak install and "restore my games" from the Windows Steam library.

Nothing here writes to Windows: the Windows partition is only ever read (mounted read-only by the caller).
The owner's game list is read at runtime from their own Steam library; no game list ships in the image.
"""
from __future__ import annotations
import glob, json, os, re

SHARE = os.environ.get("JARVIS_OS_APPS_SHARE", "/usr/share/jarvis-os/apps")
STATE = os.path.join(os.path.expanduser("~"), ".local", "state", "jarvis")

# ---------- Flatpaks ----------

def load_list(path):
    """One Flathub app id per line; '#' starts a comment."""
    ids = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if line and re.fullmatch(r"[A-Za-z0-9_.-]+(\.[A-Za-z0-9_-]+){2,}", line):
            ids.append(line)
    return ids

def installed_flatpaks(run):
    rc, out = run(["flatpak", "list", "--app", "--columns=application"], timeout=60)
    return set(out.split()) if rc == 0 else set()

def install_flatpaks(run, ids, notify=None):
    """Installs each missing app for this user from Flathub. Returns {app_id: 'ok'|'already'|'failed'}."""
    notify = notify or (lambda msg: None)
    run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
         "https://dl.flathub.org/repo/flathub.flatpakrepo"], timeout=120)
    have = installed_flatpaks(run)
    result = {}
    todo = [i for i in ids if i not in have]
    for i in ids:
        if i in have:
            result[i] = "already"
    for n, app in enumerate(todo, 1):
        notify(f"Installing {app} ({n} of {len(todo)})")
        rc, _ = run(["flatpak", "install", "--user", "--noninteractive", "-y", "flathub", app], timeout=1800)
        result[app] = "ok" if rc == 0 else "failed"
    return result

# ---------- Steam: read the Windows library, plan the restore ----------

_KV = re.compile(r'"([^"]+)"\s+"([^"]*)"')

def parse_acf(text):
    """The flat key/values of a Steam appmanifest (nested blocks are ignored)."""
    out = {}
    for k, v in _KV.findall(text):
        out.setdefault(k.lower(), v)
    return out

def library_paths(libraryfolders_vdf_text):
    return [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', libraryfolders_vdf_text)]

def windows_to_mount(path, mounts):
    """'D:\\SteamLibrary' -> '/run/media/u/Games/SteamLibrary' given {'D': '/run/media/u/Games'}."""
    m = re.match(r"^([A-Za-z]):[\\/]*(.*)$", path)
    if not m:
        return None
    parts = [x for x in m.group(2).replace("\\", "/").split("/") if x]
    letter = m.group(1).upper()
    roots = [mounts[letter]] if letter in mounts else list(mounts.values())   # Linux can't see drive letters
    for root in roots:
        hit = _ci(root, *parts) if parts else root
        if hit:
            return hit
    return None

def _ci(root, *parts):
    """Case-insensitive path join (NTFS names keep Windows casing)."""
    cur = root
    for part in parts:
        if not os.path.isdir(cur):
            return None
        hit = [e for e in os.listdir(cur) if e.lower() == part.lower()]
        if not hit:
            return None
        cur = os.path.join(cur, hit[0])
    return cur

def find_windows_steam(mounts):
    """mounts: {'C': '/run/media/u/Windows', ...}. Returns a list of games
    {appid, name, installdir, size, library} read from the Windows Steam libraries."""
    libs = []
    for letter, root in mounts.items():
        for base in (("Program Files (x86)", "Steam"), ("Program Files", "Steam")):
            steam = _ci(root, *base)
            vdf = steam and _ci(steam, "steamapps", "libraryfolders.vdf")
            if vdf:
                libs += [p for p in (windows_to_mount(x, mounts) for x in library_paths(open(vdf, encoding="utf-8", errors="replace").read())) if p]
                libs.append(steam)
    games, seen = [], set()
    for lib in dict.fromkeys(libs):
        apps = _ci(lib, "steamapps")
        if not apps:
            continue
        for acf in sorted(glob.glob(os.path.join(apps, "appmanifest_*.acf"))):
            kv = parse_acf(open(acf, encoding="utf-8", errors="replace").read())
            if kv.get("appid") and kv["appid"] not in seen:
                seen.add(kv["appid"])
                games.append({"appid": kv["appid"], "name": kv.get("name", kv["appid"]),
                              "installdir": kv.get("installdir", ""), "size": int(kv.get("sizeondisk", "0") or 0),
                              "library": lib})
    return games

def load_compat(path=None):
    path = path or os.path.join(SHARE, "steam-compat.json")
    try:
        return json.load(open(path, encoding="utf-8"))
    except OSError:
        return {}

def plan_restore(games, compat, linux_steamapps):
    """Returns (install, skip, tweaks): what to queue, what stays on Windows (with why), and launch-option tips."""
    install, skip, tweaks = [], [], []
    have = {os.path.basename(p)[12:-4] for p in glob.glob(os.path.join(linux_steamapps, "appmanifest_*.acf"))}
    for g in games:
        c = compat.get(g["appid"], {})
        if c.get("status") == "windows-only":
            skip.append({**g, "why": c.get("why", "does not run on Linux")})
        elif c.get("status") == "skip":
            continue
        elif g["appid"] in have:
            continue
        else:
            install.append(g)
            if c.get("launch_options"):
                tweaks.append({"appid": g["appid"], "name": g["name"], "launch_options": c["launch_options"],
                               "why": c.get("why", "")})
    return install, skip, tweaks

def stub_manifest(appid, name, installdir):
    """A minimal appmanifest that makes Steam queue the download on its next start (StateFlags 1026 =
    update required + update started). Steam fills in the rest itself."""
    safe = (name or appid).replace('"', "'")
    inst = (installdir or safe).replace('"', "'")
    return ('"AppState"\n{\n'
            f'\t"appid"\t\t"{appid}"\n\t"Universe"\t\t"1"\n\t"name"\t\t"{safe}"\n'
            f'\t"StateFlags"\t\t"1026"\n\t"installdir"\t\t"{inst}"\n'
            '\t"LastUpdated"\t\t"0"\n\t"SizeOnDisk"\t\t"0"\n\t"BytesToDownload"\t\t"0"\n\t"BytesDownloaded"\t\t"0"\n'
            '\t"AutoUpdateBehavior"\t\t"0"\n\t"AllowOtherDownloadsWhileRunning"\t\t"0"\n\t"ScheduledAutoUpdate"\t\t"0"\n}\n')

def queue_downloads(install, linux_steamapps):
    """Writes stub manifests for games Steam doesn't know yet (call with Steam closed). Returns the appids written."""
    os.makedirs(linux_steamapps, exist_ok=True)
    written = []
    for g in install:
        p = os.path.join(linux_steamapps, f"appmanifest_{g['appid']}.acf")
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write(stub_manifest(g["appid"], g["name"], g["installdir"]))
            written.append(g["appid"])
    return written

def linux_steamapps():
    for p in ("~/.local/share/Steam/steamapps", "~/.steam/steam/steamapps",
              "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps"):
        p = os.path.expanduser(p)
        if os.path.isdir(os.path.dirname(p)):
            return p
    return os.path.expanduser("~/.local/share/Steam/steamapps")

def ntfs_partitions(run):
    """[(device, label)] for NTFS partitions (candidates for the Windows drive)."""
    rc, out = run(["lsblk", "-J", "-o", "PATH,FSTYPE,LABEL,MOUNTPOINT"], timeout=15)
    if rc != 0:
        return []
    def walk(nodes):
        for n in nodes:
            yield n
            yield from walk(n.get("children") or [])
    return [(n["path"], n.get("label") or "", n.get("mountpoint")) for n in walk(json.loads(out).get("blockdevices", []))
            if (n.get("fstype") or "").lower() == "ntfs"]
