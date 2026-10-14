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
    "numpy>=1.24" "PySide6-Essentials>=6.6,<6.12" "psutil>=5.9" "rapidfuzz>=3.0" "piper-tts>=1.3" \
    "websocket-client>=1.6" "opencv-contrib-python-headless>=4.8,<5"
# openWakeWord's tflite dependency has no wheels; jarvisd only uses its ONNX models
/tmp/uvenv/bin/uv pip install --python /usr/lib/jarvis-os/venv/bin/python --no-deps "openwakeword==0.6.0"
# Offline from the first boot: the speech-to-text model and JARVIS's voice ship in the image (~200 MB);
# only the optional offline chat model (Ollama, a few GB) is downloaded later, into the person's home folder.
HF_HOME=/tmp/hf /usr/lib/jarvis-os/venv/bin/python - <<'EOF'
from faster_whisper import download_model
p = download_model("base.en", output_dir="/usr/lib/jarvis-os/models/whisper/base.en")
print("whisper base.en ->", p)
EOF
V=https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/medium/en_GB-alan-medium.onnx
mkdir -p /usr/lib/jarvis-os/voices
curl -fsSL --retry 5 -o /usr/lib/jarvis-os/voices/en_GB-alan-medium.onnx "$V"
curl -fsSL --retry 5 -o /usr/lib/jarvis-os/voices/en_GB-alan-medium.onnx.json "$V.json"
test -s /usr/lib/jarvis-os/models/whisper/base.en/model.bin && test -s /usr/lib/jarvis-os/voices/en_GB-alan-medium.onnx
rm -rf /tmp/hf /usr/lib/jarvis-os/models/whisper/base.en/.cache
rm -rf /tmp/uvenv
cp -a /ctx/jarvisd/jarvisd /usr/lib/jarvis-os/jarvisd
install -Dm0755 /dev/stdin /usr/bin/jarvisctl <<'EOF'
#!/bin/sh
cd /usr/lib/jarvis-os && exec /usr/lib/jarvis-os/venv/bin/python -m jarvisd "$@"
EOF

# 4. Services: per-user daemon + indicator for every user; uinput access for ydotool
systemctl --global enable jarvisd.service jarvis-indicator.service jarvis-hud.service jarvis-link.service ydotoold.service jarvis-first-boot-apps.service jarvis-first-login-tuning.service jarvis-ollama.service jarvis-ai-setup.service
systemctl enable jarvis-grub-theme.service jarvis-boot-names.service

# 4b. Look: JARVIS colours by default for every user (merged into, not replacing, the distro's kdeglobals);
#     the wallpaper is set per user by usr/share/plasma/shells/.../updates/jarvis-os-wallpaper.js
kwriteconfig6 --file /etc/xdg/kdeglobals --group General --key ColorScheme JarvisVibranium
kwriteconfig6 --file /etc/xdg/kdeglobals --group KDE --key LookAndFeelPackage org.kde.breezedark.desktop

# 5. Identity: keep ID=bazzite so ujust/Bazzite tooling keeps working; brand the names
sed -i -e 's/^PRETTY_NAME=.*/PRETTY_NAME="JARVIS OS '"${JARVIS_OS_VERSION}"' (Vibranium)"/' \
       -e 's/^NAME=.*/NAME="JARVIS OS"/' /usr/lib/os-release
echo "JARVIS_OS_VERSION=${JARVIS_OS_VERSION}" > /usr/lib/jarvis-os/version
# default computer name (the installer and Settings can change it); the base ships "bazzite"
echo jarvis-os > /etc/hostname

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

# 8. Login screen: the JARVIS wallpaper on Plasma Login Manager's greeter (the base's display manager since
#    Fedora 44; it reads [Greeter][Wallpaper][org.kde.image][General] Image= from /etc/plasmalogin.conf). Any
#    drop-in that sets a greeter wallpaper gets the same value, so whichever file wins, it's JARVIS.
WP=file:///usr/share/wallpapers/JARVIS/contents/images/2560x1440.png
for f in /etc/plasmalogin.conf /etc/plasmalogin.conf.d/*.conf; do
  [ "$f" = /etc/plasmalogin.conf ] || grep -q 'Wallpaper' "$f" 2>/dev/null || continue
  kwriteconfig6 --file "$f" --group Greeter --key WallpaperPluginId org.kde.image
  kwriteconfig6 --file "$f" --group Greeter --group Wallpaper --group org.kde.image --group General --key Image "$WP"
  echo "login screen wallpaper set in $f"; cat "$f"
done
# older bases with SDDM: same picture behind its theme
for t in /usr/share/sddm/themes/*/; do
  [ -f "${t}theme.conf" ] || continue
  rm -f "${t}theme.conf.user"
  printf '[General]\nbackground=/usr/share/wallpapers/JARVIS/contents/images/2560x1440.png\ntype=image\n' > "${t}theme.conf.user"
done

# 9. First login is JARVIS OS, not Bazzite: the base's welcome app (Bazzite Portal / yafti) no longer autostarts.
#    It stays installed and can still be opened from the app menu.
for f in /etc/xdg/autostart/*.desktop /usr/etc/xdg/autostart/*.desktop /etc/skel/.config/autostart/*.desktop; do
  [ -f "$f" ] || continue
  if [ "$(basename "$f")" = bazzite-portal.desktop ] || grep -qiE '^(Name|Exec)=.*(bazzite.?portal|yafti)' "$f"; then
    echo "welcome app autostart off: $f"; sed -i '/^Hidden=/d' "$f"; echo 'Hidden=true' >> "$f"
  fi
done

# 10. Boot splash: the JARVIS Plymouth theme (usr/share/plymouth/themes/jarvis, art from tools/make_plymouth_art.py,
#     two-step module like Fedora's own spinner) as the default, baked into the initramfs, because the splash is
#     drawn from the initramfs before the system is mounted. The initramfs is rebuilt the way the base builds it
#     (no-hostonly, ostree); the build fails if the theme isn't inside it. A disk-password prompt still works
#     (entry/bullet/lock pictures); Esc during boot still shows the text messages.
PLY=/usr/share/plymouth/themes/jarvis
ls /usr/lib64/plymouth/two-step.so >/dev/null 2>&1 || dnf5 -y install plymouth-plugin-two-step
for f in capslock.png keyboard.png keymap-render.png; do      # optional indicator pictures from the stock spinner
  [ -f "$PLY/$f" ] || [ ! -f "/usr/share/plymouth/themes/spinner/$f" ] || cp "/usr/share/plymouth/themes/spinner/$f" "$PLY/$f"
done
echo "previous splash: $(plymouth-set-default-theme 2>/dev/null || grep -h ^Theme= /usr/share/plymouth/plymouthd.defaults 2>/dev/null || echo unknown)"
mkdir -p /etc/plymouth
printf '[Daemon]\nTheme=jarvis\nShowDelay=0\nDeviceTimeout=8\n' > /etc/plymouth/plymouthd.conf
command -v plymouth-set-default-theme >/dev/null && echo "splash now: $(plymouth-set-default-theme)"
KVERS=$(ls /usr/lib/modules)
[ "$(echo "$KVERS" | wc -w)" = 1 ] || { echo "expected one kernel, found: $KVERS"; exit 1; }
dracut --no-hostonly --kver "$KVERS" --reproducible --zstd -v --add ostree -f "/usr/lib/modules/$KVERS/initramfs.img"
chmod 0600 "/usr/lib/modules/$KVERS/initramfs.img"
lsinitrd "/usr/lib/modules/$KVERS/initramfs.img" | grep -E 'plymouth/themes/jarvis/(jarvis.plymouth|watermark.png)|two-step.so' | tee /tmp/ply.txt
[ "$(wc -l < /tmp/ply.txt)" -ge 3 ] || { echo "JARVIS splash missing from the initramfs"; exit 1; }
lsinitrd -f usr/lib/modprobe.d/90-jarvis-nvidia-kms.conf "/usr/lib/modules/$KVERS/initramfs.img" | grep -q "nvidia-drm modeset=1" \
  || { echo "NVIDIA modeset option missing from the initramfs"; exit 1; }
echo "NVIDIA modeset option is in the initramfs"
