"""RGB through OpenRGB's CLI (OpenRGB 1.0 includes a HYTE Nexus controller for the THICC Q60 and the
Nexus Portal NP50; other HYTE gear may need HYTE's own Nexus 3 app, which has a Linux build).
Profiles are saved in the OpenRGB GUI ("Vibranium", "Gaming", "Off"); voice picks one or sets a colour."""
from __future__ import annotations

COLORS = {"purple": "8000FF", "violet": "B060FF", "vibranium": "B060FF", "pink": "FF5CD6", "red": "FF0000", "blue": "0050FF",
          "cyan": "00E1FF", "green": "00FF40", "white": "FFFFFF", "orange": "FF8C1E", "yellow": "FFD200", "gold": "FFC440"}

def argv_for(color=None, profile=None, off=False, brightness=None):
    if off:
        return ["openrgb", "--noautoconnect", "--mode", "static", "--color", "000000"]
    if profile:
        return ["openrgb", "--noautoconnect", "--profile", profile]
    hexv = COLORS.get((color or "").lower())
    if not hexv:
        raise KeyError(color)
    argv = ["openrgb", "--noautoconnect", "--mode", "static", "--color", hexv]
    if brightness is not None:
        argv += ["--brightness", str(max(0, min(100, int(brightness))))]
    return argv
