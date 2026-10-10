#!/usr/bin/env python3
"""Refresh jarvisd/core from a Windows JARVIS source tree and update VENDORED.json (merged: entries for files not
copied here stay as they are). Usage: sync_core.py PATH/TO/JARVIS [--v56]
--v56 also copies the v5.6 3D files (hologram workshop, 3D brain, vitals HUD, update countdown) and re-applies the
JARVIS OS patches listed in PATCHES (each must apply, or the sync stops)."""
import hashlib, json, os, shutil, sys
root = sys.argv[1]; src = os.path.join(root, "jarvis"); dst = os.path.join(os.path.dirname(__file__), "..", "jarvisd", "jarvisd", "core")
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
PATCHES = {
    "holo.py": [("        from .safety import check_prompt\n        ok, kind = check_prompt(text)\n    except Exception:\n        ok, kind = True, None\n",
                 "        from ..safety import check_prompt\n        ok, kind = check_prompt(text)\n    except Exception:\n        return False, \"checks_unavailable\"\n")],
    "holoview.py": [("drawn with an Iron Man-style hologram look", "drawn with a sci-fi hologram look")],
    "brain3d.py": [("and an Iron Man arc reactor core", "and an original arc reactor core")],
}
files = ["commands.py", "qol.py", "wake.py"] + (["holo.py", "holoview.py", "brain3d.py", "brainbus.py", "brainview.py", "hud56.py"] if "--v56" in sys.argv else [])
mp = os.path.join(dst, "VENDORED.json")
man = json.load(open(mp)) if os.path.exists(mp) else {"files": {}}
for m in files:
    text = open(os.path.join(src, m), encoding="utf-8").read()
    for a, b in PATCHES.get(m, []):
        if a not in text:
            sys.exit(f"patch for {m} no longer applies: {a[:60]!r}")
        text = text.replace(a, b)
    open(os.path.join(dst, m), "w", encoding="utf-8").write(text)
    man["files"][m] = sha(os.path.join(dst, m))
if "--v56" in sys.argv:
    os.makedirs(os.path.join(dst, "..", "assets"), exist_ok=True)
    shutil.copy(os.path.join(root, "assets", "coastlines_110m.json"), os.path.join(dst, "..", "assets", "coastlines_110m.json"))
ver = open(os.path.join(src, "__init__.py")).read()
man["source"] = "JARVIS (Windows) " + ver.strip().splitlines()[-1]
json.dump(man, open(mp, "w"), indent=1)
print("synced", {m: man["files"][m] for m in files})
