#!/usr/bin/env bash
# Runs inside the image build. Everything lands in /usr (immutable, versioned with the image) or /etc defaults.
set -euxo pipefail
: "${JARVIS_OS_VERSION:=0.1.0}"

# 1. Files: theme, wallpapers, fonts, systemd units, polkit, helpers
cp -a /ctx/system_files/. /
chmod 0755 /usr/libexec/jarvis-os/*

# 2. Packages layered on Bazzite (each is in Fedora's repos; OpenRGB may already be present on Bazzite)
dnf5 -y install openrgb ydotool playerctl wl-clipboard python3-pip efibootmgr espeak-ng || dnf5 -y install ydotool playerctl wl-clipboard python3-pip efibootmgr espeak-ng
rpm -q openrgb || echo "WARN: openrgb not installed from repos; jarvisd falls back to the OpenRGB Flatpak"

# 3. The JARVIS daemon in its own venv under /usr/lib (read-only at runtime, replaced atomically by updates)
# Pinned Python 3.11 (the version jarvisd is tested on) via uv, independent of Fedora's system Python.
python3 -m venv /tmp/uvenv && /tmp/uvenv/bin/pip install --no-cache-dir uv
export UV_PYTHON_INSTALL_DIR=/usr/lib/jarvis-os/python UV_NO_CACHE=1
/tmp/uvenv/bin/uv venv --python 3.11 /usr/lib/jarvis-os/venv
/tmp/uvenv/bin/uv pip install --python /usr/lib/jarvis-os/venv/bin/python \
    "onnxruntime>=1.17,<2" "faster-whisper>=1.0.3" "sounddevice>=0.4.6" "scipy>=1.10" tqdm requests "scikit-learn>=1.3" \
    "numpy>=1.24" "PySide6-Essentials>=6.6,<6.12" "psutil>=5.9" "rapidfuzz>=3.0" "piper-tts>=1.3"
# openWakeWord's tflite dependency has no wheels; jarvisd only uses its ONNX models
/tmp/uvenv/bin/uv pip install --python /usr/lib/jarvis-os/venv/bin/python --no-deps "openwakeword==0.6.0"
rm -rf /tmp/uvenv
cp -a /ctx/jarvisd/jarvisd /usr/lib/jarvis-os/jarvisd
install -Dm0755 /dev/stdin /usr/bin/jarvisctl <<'EOF'
#!/bin/sh
cd /usr/lib/jarvis-os && exec /usr/lib/jarvis-os/venv/bin/python -m jarvisd "$@"
EOF

# 4. Services: per-user daemon + indicator for every user; uinput access for ydotool
systemctl --global enable jarvisd.service jarvis-indicator.service jarvis-hud.service jarvis-link.service ydotoold.service jarvis-first-boot-apps.service jarvis-first-login-tuning.service
systemctl enable jarvis-grub-theme.service

# 4b. Look: JARVIS colours by default for every user (merged into, not replacing, the distro's kdeglobals);
#     the wallpaper is set per user by usr/share/plasma/shells/.../updates/jarvis-os-wallpaper.js
kwriteconfig6 --file /etc/xdg/kdeglobals --group General --key ColorScheme JarvisVibranium
kwriteconfig6 --file /etc/xdg/kdeglobals --group KDE --key LookAndFeelPackage org.kde.breezedark.desktop

# 5. Identity: keep ID=bazzite so ujust/Bazzite tooling keeps working; brand the names
sed -i -e 's/^PRETTY_NAME=.*/PRETTY_NAME="JARVIS OS '"${JARVIS_OS_VERSION}"' (Vibranium)"/' \
       -e 's/^NAME=.*/NAME="JARVIS OS"/' /usr/lib/os-release
echo "JARVIS_OS_VERSION=${JARVIS_OS_VERSION}" > /usr/lib/jarvis-os/version

# 6. Update trust: only images signed with this project's cosign key are accepted by `bootc upgrade/switch`
: "${IMAGE_REPO:=ghcr.io/example/jarvis-os}"
install -Dm0644 /ctx/cosign.pub /etc/pki/containers/jarvis-os.pub
python3 - "$IMAGE_REPO" <<'EOF'
import json, sys
repo = sys.argv[1]
p = "/etc/containers/policy.json"
pol = json.load(open(p))
pol.setdefault("transports", {}).setdefault("docker", {})[repo] = [
    {"type": "sigstoreSigned", "keyPath": "/etc/pki/containers/jarvis-os.pub", "signedIdentity": {"type": "matchRepository"}}]
json.dump(pol, open(p, "w"), indent=2)
EOF
install -Dm0644 /dev/stdin "/etc/containers/registries.d/jarvis-os.yaml" <<EOF
docker:
  ${IMAGE_REPO}:
    use-sigstore-attachments: true
EOF
echo "IMAGE_REPO=${IMAGE_REPO}" >> /usr/lib/jarvis-os/version

# 7. Repo hygiene: a repo whose GPG key file is missing breaks the installer build (bootc-image-builder
#    depsolves Anaconda from these repos); disable such repos. Updates come as whole signed images anyway.
python3 /ctx/fix_repos.py

# 8. Login screen: the JARVIS wallpaper behind whichever SDDM theme the base ships
for t in /usr/share/sddm/themes/*/; do
  [ -f "${t}theme.conf" ] || continue
  ls -l "${t}theme.conf.user" 2>/dev/null || :   # Fedora links this into /var, which an image can't ship: replace the link
  rm -f "${t}theme.conf.user"
  printf '[General]\nbackground=/usr/share/wallpapers/JARVIS/contents/images/2560x1440.png\ntype=image\n' > "${t}theme.conf.user"
  echo "login screen wallpaper: ${t}theme.conf.user"
done

# 9. First login is JARVIS OS, not Bazzite: the base's welcome app (Bazzite Portal / yafti) no longer autostarts.
#    It stays installed and can still be opened from the app menu.
for f in /etc/xdg/autostart/*.desktop /usr/etc/xdg/autostart/*.desktop /etc/skel/.config/autostart/*.desktop; do
  [ -f "$f" ] || continue
  if [ "$(basename "$f")" = bazzite-portal.desktop ] || grep -qiE '^(Name|Exec)=.*(bazzite.?portal|yafti)' "$f"; then
    echo "welcome app autostart off: $f"; sed -i '/^Hidden=/d' "$f"; echo 'Hidden=true' >> "$f"
  fi
done
