#!/usr/bin/bash
# JARVIS OS installer branding (kickstart %pre, runs before the installer's window opens). Puts the JARVIS logo and
# the Vibranium colours over the base's installer pictures: the sidebar background, the sidebar logo and the spoke
# top bar. Cosmetic only: it never touches a disk, never fails the install (every error is ignored), and the
# pictures are replaced by file name, so it also works when the installer has already read its style sheet.
# Source of truth: this file + image/installer/art/*.png (made by tools/make_installer_art.py);
# tools/sync_installer_guard.py embeds both into iso.toml and install-test.toml (the art lines below).
set +e
LOG=/tmp/jarvis-installer-branding.log; : > "$LOG"
ART=/tmp/jarvis-installer-art; mkdir -p "$ART"
art() { base64 -d > "$ART/$1" 2>>"$LOG"; }
@ART@
for name in sidebar-bg sidebar-logo topbar-bg; do
  [ -s "$ART/$name.png" ] || { echo "missing $name.png" >> "$LOG"; continue; }
  # every installer picture with this name (the base ships its own under /usr/share/anaconda), plus any picture a
  # style sheet points at under this name
  { find /usr/share/anaconda -name "$name*.png" 2>/dev/null
    grep -ho "url([^)]*$name[^)]*\.png" /usr/share/anaconda/*.css /usr/share/anaconda/*/*.css 2>/dev/null | sed "s/^url(['\"]*//; s/^file:\/\///" ; } | sort -u |
  while read -r f; do [ -f "$f" ] && cp "$ART/$name.png" "$f" && echo "branded $f" >> "$LOG"; done
done
# colours behind the pictures (used if the installer reads its style sheet after this point)
for css in /usr/share/anaconda/anaconda-gtk.css; do
  [ -f "$css" ] && printf '\n/* JARVIS OS */\n.logo-sidebar { background-color: #090413; }\nAnacondaSpokeWindow #nav-box { background-color: #160a2c; }\n' >> "$css" && echo "colours $css" >> "$LOG"
done
exit 0
