#!/usr/bin/env python3
"""Tiny VM driver for the installer click-through test: QMP mouse clicks + OCR-located targets.
  vmctl.py QMP_SOCK click X Y                 left click at screen pixel X,Y (usb-tablet, absolute)
  vmctl.py QMP_SOCK clickword PNG WORD [N]    OCR PNG (tesseract tsv), click the centre of the Nth match of WORD
                                              (a word or a two-word phrase, case-insensitive); exit 1 if not found
  vmctl.py - find PNG WORD                    print "x y" of WORD's centre, exit 1 if not found
"""
import json, socket, subprocess, sys, time
import struct

def size(png):
    b = open(png, 'rb').read(24); return struct.unpack('>II', b[16:24])   # PNG IHDR width, height

def qmp(sock, cmds):
    s = socket.socket(socket.AF_UNIX); s.connect(sock); f = s.makefile("rw")
    json.loads(f.readline())
    for c in [{"execute": "qmp_capabilities"}] + cmds:
        f.write(json.dumps(c) + "\n"); f.flush()
        while True:
            r = json.loads(f.readline())
            if "return" in r or "error" in r:
                if "error" in r: print("qmp error", r, file=sys.stderr)
                break
    s.close()

def click(sock, x, y, w, h):
    ax, ay = int(x * 32767 / max(1, w - 1)), int(y * 32767 / max(1, h - 1))
    mv = {"execute": "input-send-event", "arguments": {"events": [
        {"type": "abs", "data": {"axis": "x", "value": ax}}, {"type": "abs", "data": {"axis": "y", "value": ay}}]}}
    btn = lambda down: {"execute": "input-send-event", "arguments": {"events": [
        {"type": "btn", "data": {"down": down, "button": "left"}}]}}
    qmp(sock, [mv]); time.sleep(0.4); qmp(sock, [btn(True)]); time.sleep(0.15); qmp(sock, [btn(False)])

def find(png, phrase, nth=1):
    out = subprocess.run(["tesseract", png, "-", "tsv"], capture_output=True, text=True).stdout.splitlines()
    words = []
    for line in out[1:]:
        p = line.split("\t")
        if len(p) == 12 and p[11].strip():
            words.append((p[11].strip().lower().strip(".,:;!?\"'()"), int(p[6]), int(p[7]), int(p[8]), int(p[9])))
    want = phrase.lower().split(); hits = []
    for i in range(len(words) - len(want) + 1):
        if all(words[i + k][0] == want[k] for k in range(len(want))):
            ws = words[i:i + len(want)]
            x0, y0 = min(w[1] for w in ws), min(w[2] for w in ws)
            x1, y1 = max(w[1] + w[3] for w in ws), max(w[2] + w[4] for w in ws)
            hits.append(((x0 + x1) // 2, (y0 + y1) // 2))
    return hits[nth - 1] if len(hits) >= nth else None

if __name__ == "__main__":
    sock, cmd = sys.argv[1], sys.argv[2]
    if cmd == "click":
        png = sys.argv[5] if len(sys.argv) > 5 else None
        w, h = size(png) if png else (1280, 800)
        click(sock, int(sys.argv[3]), int(sys.argv[4]), w, h)
    elif cmd in ("clickword", "find"):
        png, word = sys.argv[3], sys.argv[4]; nth = int(sys.argv[5]) if len(sys.argv) > 5 else 1
        hit = find(png, word, nth)
        if not hit: print(f"not found: {word}"); sys.exit(1)
        print(*hit)
        if cmd == "clickword":
            w, h = size(png); click(sock, hit[0], hit[1], w, h)
