#!/usr/bin/env python3
"""Tab side: make a one-time JARVIS OS link code (see jarvisd/jarvisd/linkcode.py).

  python3 tools/make_link_code.py SETTINGS.json      # prints the code; the person types it on JARVIS OS:
                                                      #   jarvisctl link --code XXXX-XXXX-XXXX
SETTINGS.json is shaped like Windows JARVIS's config.json (only "forwarding", "replies", "assistant" and
"briefing" are used). It is private: keep it out of this repository. The code is good for one hour.
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "jarvisd"))
from jarvisd import linkcode  # noqa: E402

def main(argv):
    if len(argv) != 2:
        print(__doc__); return 2
    settings = json.load(open(argv[1], encoding="utf-8-sig"))
    if not (settings.get("replies") or {}).get("key") or not (settings.get("forwarding") or {}).get("to"):
        print("settings need replies.key and forwarding.to"); return 1
    code = linkcode.new_code()
    linkcode.publish(code, settings)
    print(code)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
