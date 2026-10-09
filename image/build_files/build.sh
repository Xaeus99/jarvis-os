#!/usr/bin/env bash
# Runs inside the image build. Everything lands in /usr (immutable, versioned with the image) or /etc defaults.
set -euxo pipefail
: "${JARVIS_OS_VERSION:=0.1.0}"

# 1. Files: theme, wallpapers, fonts, systemd units, polkit, helpers
cp -a /ctx/system_files/. /
chmod 0755 /usr/libexec/jarvis-os/*

# 2. Packages layered on Bazzite (each is in Fedora's repos; OpenRGB may already be present on Bazzite)
dnf5 -y install openrgb ydotool playerctl wl-clipboard python3-pip efibootmgr || dnf5 -y install ydotool playerctl wl-clipboard python3-pip efibootmgr
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
systemctl --global enable jarvisd.service jarvis-indicator.service ydotoold.service jarvis-first-boot-apps.service
systemctl enable jarvis-grub-theme.service

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
