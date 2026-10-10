#!/bin/bash
# Usage: boot_test.sh DISK.qcow2 OUTDIR — boots under KVM/OVMF, screendumps every 20 s, logs in as the test user.
set -u
DISK=$1; OUT=$2; mkdir -p "$OUT"
cp /usr/share/OVMF/OVMF_VARS_4M.fd /tmp/vars.fd
MON=/tmp/qmon.sock
qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 4 -m 8192 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=/tmp/vars.fd \
  -drive file="$DISK",if=virtio,format=qcow2 \
  -device virtio-vga -display none -nic user,model=virtio-net-pci \
  -monitor unix:$MON,server,nowait -chardev socket,id=ser0,path=/tmp/qser.sock,server=on,wait=off,logfile="$OUT/serial.log" -serial chardev:ser0 &
QPID=$!
mon() { echo "$1" | socat - UNIX-CONNECT:$MON >/dev/null 2>&1; }
shot() { mon "screendump /tmp/s.ppm"; sleep 1; convert /tmp/s.ppm "$OUT/$1.png" 2>/dev/null && echo "shot $1"; }
typekeys() { # QEMU sendkey names for a plain lowercase/digit/dash string
  local s=$1 i c
  for ((i=0; i<${#s}; i++)); do c=${s:i:1}; [ "$c" = "-" ] && c=minus; mon "sendkey $c"; sleep 0.15; done; }
sleep 4; shot 00_firmware
for t in 1 2 3 4 5 6 7 8 9 10 11 12; do sleep 20; shot "$(printf '%02d' $t)_boot_$((t*20))s"; done
# login screen should be up by now (~4 min under KVM): type the test user's password
mon "sendkey ret"; sleep 2
typekeys "jarvis-boot-test"; mon "sendkey ret"
for t in 1 2 3 4 5 6 7 8 9; do sleep 20; shot "$(printf '%02d' $((12+t)))_desktop_$((t*20))s"; done
# diagnostics over the serial console: log in as the test user and dump what failed
ser() { printf '%s\r' "$1" | socat - UNIX-CONNECT:/tmp/qser.sock >/dev/null 2>&1; sleep "${2:-2}"; }
ser ""; ser "jarvis" 3; ser "jarvis-boot-test" 6
ser "echo jarvis-boot-test | sudo -S true" 4
ser "sudo systemctl --no-pager --failed; sudo journalctl -b --no-pager -u jarvis-grub-theme -u tuned-ppd | tail -40" 6
ser "systemctl --no-pager --machine=jarvis@ --user status jarvisd jarvis-first-boot-apps jarvis-first-login-tuning 2>&1 | head -60" 6
ser "cat /usr/lib/jarvis-os/version; rpm-ostree status --booted | head -8" 4
mon "quit"; wait $QPID 2>/dev/null
ls -la "$OUT"
