#!/bin/bash
# Boots the real (interactive) installer ISO under KVM/OVMF next to a blank disk and screenshots it up to the
# installer's first screen. Nothing is installed: the VM is switched off once the installer is up.
#   iso_boot_test.sh ISO OUTDIR   -> OUTDIR/*.png, OUTDIR/serial.log, OUTDIR/verdict.txt; exit 0 = installer screen seen
# "Installer screen seen" = OCR (tesseract) finds the installer's own words (language / welcome / installation
# summary / keyboard) on a screenshot taken after the boot menu. One retry (fresh VM) if the first boot stalls,
# because the installer's own early boot has hung once before at "Switch Root" (install-test run 38025874881).
set -u
ISO=$1; OUT=$2; mkdir -p "$OUT"
MON=/tmp/qmon-iso.sock
mon() { echo "$1" | socat - UNIX-CONNECT:$MON >/dev/null 2>&1; }
shot() { mon "screendump /tmp/iso.ppm"; sleep 0.7; convert /tmp/iso.ppm "$OUT/$1.png" 2>/dev/null && echo "shot $1"; }
alive() { kill -0 "$QPID" 2>/dev/null; }
seen() { # OCR a screenshot for the installer's words
  tesseract "$OUT/$1.png" - 2>/dev/null | tr '[:upper:]' '[:lower:]' > "$OUT/$1.txt"
  grep -Eq "what language|welcome to|installation summary|installation destination|keyboard layout|language support|time & date|root account|user creation" "$OUT/$1.txt"; }
attempt() { # attempt N
  local n=$1 t=0
  rm -f /tmp/iso-vars.fd; cp /usr/share/OVMF/OVMF_VARS_4M.fd /tmp/iso-vars.fd
  rm -f /tmp/iso-blank.qcow2; qemu-img create -q -f qcow2 /tmp/iso-blank.qcow2 200G
  qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 4 -m 8192 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file=/tmp/iso-vars.fd \
    -drive file=/tmp/iso-blank.qcow2,if=none,format=qcow2,id=hd -device virtio-blk-pci,drive=hd,bootindex=2 \
    -drive file="$ISO",if=none,media=cdrom,id=cd,readonly=on -device ide-cd,drive=cd,bus=ide.1,bootindex=1 \
    -device virtio-vga -display none -nic user,model=virtio-net-pci -usb -device usb-tablet \
    -monitor unix:$MON,server,nowait -serial file:"$OUT/serial_$n.log" &
  QPID=$!; sleep 4
  shot "a${n}_00_firmware"; sleep 10; shot "a${n}_01_boot_menu"
  mon "sendkey up"; sleep 0.5; mon "sendkey ret"      # "Install ..." (skips the media check, as a person would)
  while alive && [ $t -lt 720 ]; do
    sleep 20; t=$((t+20)); s=$(printf 'a%d_%02d_%04ds' "$n" $((t/20+1)) $t); shot "$s"
    if [ $t -ge 60 ] && seen "$s"; then
      cp "$OUT/$s.png" "$OUT/installer_screen.png"; echo "installer screen after ${t}s ($s)"
      sleep 20; shot "a${n}_settled"; cp "$OUT/a${n}_settled.png" "$OUT/installer_screen.png"
      mon "sendkey alt-c"; sleep 15; shot "a${n}_next_screen"      # "Continue" on the welcome page -> summary hub
      seen "a${n}_next_screen" && cp "$OUT/a${n}_next_screen.png" "$OUT/installer_summary.png"
      mon quit; sleep 2; echo "PASS: the installer came up after ${t}s (attempt $n)" > "$OUT/verdict.txt"; return 0
    fi
  done
  mon quit; sleep 2; alive && kill "$QPID"; return 1
}
attempt 1 || { echo "::warning::no installer screen within 12 min on attempt 1; retrying in a fresh VM"; attempt 2; } || {
  echo "FAIL: no installer screen within 12 min, twice" > "$OUT/verdict.txt"; cat "$OUT/verdict.txt"; exit 1; }
cat "$OUT/verdict.txt"; rm -f /tmp/iso-blank.qcow2
