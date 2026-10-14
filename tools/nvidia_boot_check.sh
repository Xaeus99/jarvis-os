#!/bin/bash
# What the image does for an NVIDIA RTX 50-series card at boot, read from inside the image (no GPU needed):
# kernel arguments (bootc kargs.d), nvidia modprobe options, whether the NVIDIA driver is inside the initramfs
# (early KMS: the splash and the login screen draw on the NVIDIA driver from the start), the nouveau blacklist,
# the driver version and whether it lists the RTX 5060, and the splash (Plymouth) settings.
#   nvidia_boot_check.sh IMAGE [OUT.txt]   (needs podman; runs the image with bash, nothing is booted)
set -uo pipefail
IMG=$1; OUT=${2:-/dev/stdout}
sudo podman run --rm --entrypoint bash "$IMG" -c '
K=$(ls /usr/lib/modules | sort -V | tail -1); echo "kernel $K"
echo "## kargs.d"; for f in /usr/lib/bootc/kargs.d/*.toml; do [ -f "$f" ] && echo "$f: $(tr "\n" " " < "$f")"; done
echo "## modprobe options for nvidia"; grep -rhs "^[^#]*nvidia" /usr/lib/modprobe.d /etc/modprobe.d | sort -u
echo "## nouveau"; grep -rhs "^[^#]*nouveau" /usr/lib/modprobe.d /etc/modprobe.d | sort -u
echo "## dracut config mentioning nvidia"; grep -rHs "nvidia" /usr/lib/dracut/dracut.conf.d /etc/dracut.conf.d | sed "s/^/  /"
echo "## driver"; M=$(find /usr/lib/modules/$K -name "nvidia.ko*" | head -1); echo "module $M"
[ -n "$M" ] && modinfo -F version "$M" 2>/dev/null | sed "s/^/version /" && modinfo -F license "$M" 2>/dev/null | sed "s/^/license /"
for d in nvidia-drm nvidia-modeset; do f=$(find /usr/lib/modules/$K -name "${d/-/_}.ko*" -o -name "$d.ko*" | head -1); [ -n "$f" ] && modinfo -p "$f" 2>/dev/null | grep -E "^(modeset|fbdev)" | sed "s/^/$d param: /"; done
J=$(ls /usr/share/nvidia/supported-gpus/supported-gpus.json 2>/dev/null | head -1)
if [ -n "$J" ]; then python3 - "$J" <<EOF
import json,sys
d=json.load(open(sys.argv[1])); c=d.get("chips",d)
hits=[x for x in c if "5060" in x.get("name","")]
for x in hits: print("supported-gpus:", x.get("name"), x.get("devid"), ",".join(x.get("features",[]))[:80])
print("supported-gpus: RTX 5060 listed" if hits else "supported-gpus: RTX 5060 NOT listed")
EOF
else echo "supported-gpus.json not shipped"; fi
echo "## initramfs"; I=/usr/lib/modules/$K/initramfs.img; ls -l $I
lsinitrd $I 2>/dev/null > /tmp/l.txt
echo "nvidia modules in initramfs: $(grep -Eo "nvidia[a-z_-]*\.ko[.a-z]*" /tmp/l.txt | sort -u | tr "\n" " ")"
echo "simpledrm/efifb in initramfs: $(grep -Eo "(simpledrm|simplefb|efifb)[a-z_]*\.ko[.a-z]*" /tmp/l.txt | sort -u | tr "\n" " ") (built-in: $(grep -Eo "CONFIG_DRM_SIMPLEDRM=.|CONFIG_SYSFB_SIMPLEFB=." /usr/lib/modules/$K/config 2>/dev/null | tr "\n" " "))"
echo "nvidia firmware (GSP) in initramfs: $(grep -c "firmware/nvidia/" /tmp/l.txt) files"
echo "plymouth in initramfs: $(grep -Eo "plymouth/themes/[a-z-]+/[a-z-]+\.plymouth" /tmp/l.txt | sort -u | tr "\n" " ")"
lsinitrd -f etc/modprobe.d/nvidia-modeset.conf $I 2>/dev/null | sed "s/^/initramfs modprobe: /"
lsinitrd -f usr/lib/modprobe.d/90-jarvis-nvidia-kms.conf $I 2>/dev/null | grep -v "^#" | sed "s/^/initramfs modprobe: /"
lsinitrd $I 2>/dev/null | grep -E "etc/modprobe.d/" | sed "s/^.* etc/initramfs has etc/"
echo "## plymouth"; cat /etc/plymouth/plymouthd.conf 2>/dev/null; grep -h "^Theme\|^DeviceTimeout\|^ShowDelay" /usr/share/plymouth/plymouthd.defaults 2>/dev/null | sed "s/^/defaults: /"
echo "## display manager"; systemctl get-default 2>/dev/null; ls /etc/systemd/system/display-manager.service 2>/dev/null && readlink -f /etc/systemd/system/display-manager.service
' > "$OUT" 2>&1
