#!/usr/bin/env python3
"""Refresh jarvisd/core from a Windows JARVIS source tree and rewrite VENDORED.json. Usage: sync_core.py PATH/TO/JARVIS"""
import hashlib, json, os, shutil, sys
src = os.path.join(sys.argv[1], "jarvis"); dst = os.path.join(os.path.dirname(__file__), "..", "jarvisd", "jarvisd", "core")
files = {}
for m in ("commands.py", "qol.py", "wake.py"):
    shutil.copy(os.path.join(src, m), os.path.join(dst, m))
    files[m] = hashlib.sha256(open(os.path.join(src, m), "rb").read()).hexdigest()
ver = open(os.path.join(sys.argv[1], "jarvis", "__init__.py")).read()
json.dump({"source": "JARVIS (Windows) " + ver.strip().splitlines()[-1], "files": files}, open(os.path.join(dst, "VENDORED.json"), "w"), indent=1)
print("synced", files)
