#!/usr/bin/env bash
# Build the boot-menu demo ISO and boot it in QEMU (UEFI via OVMF), saving a screenshot.
# Needs: grub-mkrescue (grub-efi-amd64-bin, grub-pc-bin), xorriso, mtools, qemu-system-x86, ovmf.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/out}"; mkdir -p "$OUT"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/boot/grub/themes"
cp "$HERE/grub.cfg" "$STAGE/boot/grub/grub.cfg"
cp -r "$HERE/../image/system_files/usr/share/grub/themes/jarvis" "$STAGE/boot/grub/themes/jarvis"
grub-mkrescue -o "$OUT/jarvis-bootmenu-demo.iso" "$STAGE" -- -volid JARVIS_DEMO >/dev/null 2>&1
ls -l "$OUT/jarvis-bootmenu-demo.iso"
