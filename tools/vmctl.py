#!/usr/bin/env python3
"""Tiny VM driver for the installer click-through test: QMP mouse clicks + OCR-located targets.
  vmctl.py QMP_SOCK click X Y                 left click at screen pixel X,Y (usb-tablet, absolute)
  vmctl.py QMP_SOCK clickword PNG WORD [N]    OCR PNG (tesseract tsv), click the centre of the Nth match of WORD
                                              (a word or a two-word phrase, case-insensitive); exit 1 if not found
  vmctl.py - find PNG WORD                    print "x y" of WORD's centre, exit 1 if not found
  vmctl.py QMP_SOCK clickafter PNG WORD DX [alone]   click DX pixels right of WORD's right edge (the text box next to
                                              a form label); "alone": only a match that is the whole OCR line
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

def words_of(png):
    out = subprocess.run(["tesseract", png, "-", "tsv"], capture_output=True, text=True).stdout.splitlines()
    words = []
    for line in out[1:]:
        p = line.split("\t")
        if len(p) == 12 and p[11].strip():   # text, left, top, width, height, (block, paragraph, line)
            words.append((p[11].strip().lower().strip(".,:;!?\"'()"), int(p[6]), int(p[7]), int(p[8]), int(p[9]), (p[2], p[3], p[4])))
    return words

def boxes(png, phrase, alone=False):
    words = words_of(png); want = phrase.lower().split(); hits = []
    for i in range(len(words) - len(want) + 1):
        if all(words[i + k][0] == want[k] for k in range(len(want))):
            ws = words[i:i + len(want)]
            if alone and sum(1 for w in words if w[5] == ws[0][5] and any(ch.isalnum() for ch in w[0])) != len(want): continue
            hits.append((min(w[1] for w in ws), min(w[2] for w in ws), max(w[1] + w[3] for w in ws), max(w[2] + w[4] for w in ws)))
    return hits

def find(png, phrase, nth=1):
    hits = [((x0 + x1) // 2, (y0 + y1) // 2) for x0, y0, x1, y1 in boxes(png, phrase)]
    return hits[nth - 1] if len(hits) >= nth else None

if __name__ == "__main__":
    sock, cmd = sys.argv[1], sys.argv[2]
    if cmd == "click":
        png = sys.argv[5] if len(sys.argv) > 5 else None
        w, h = size(png) if png else (1280, 800)
        click(sock, int(sys.argv[3]), int(sys.argv[4]), w, h)
    elif cmd == "clickafter":
        png, word, dx = sys.argv[3], sys.argv[4], int(sys.argv[5])
        hits = boxes(png, word, alone=len(sys.argv) > 6 and sys.argv[6] == "alone")
        if not hits: print(f"not found: {word}"); sys.exit(1)
        x0, y0, x1, y1 = hits[0]; x, y = x1 + dx, (y0 + y1) // 2
        print(x, y); w, h = size(png); click(sock, x, y, w, h)
    elif cmd in ("clickword", "find"):
        png, word = sys.argv[3], sys.argv[4]; nth = int(sys.argv[5]) if len(sys.argv) > 5 else 1
        hit = find(png, word, nth)
        if not hit: print(f"not found: {word}"); sys.exit(1)
        print(*hit)
        if cmd == "clickword":
            w, h = size(png); click(sock, hit[0], hit[1], w, h)
