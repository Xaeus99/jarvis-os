#!/usr/bin/env bash
# Boot the demo ISO under UEFI (OVMF) headless and screenshot the menu after N seconds.
set -euo pipefail
ISO="$1"; SHOT="$2"; WAIT="${3:-25}"
VARS="$(mktemp)"; cp /usr/share/OVMF/OVMF_VARS_4M.fd "$VARS"
MON="$(mktemp -u)"
qemu-system-x86_64 -machine q35 -m 512 -smp 2 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file="$VARS" \
  -device VGA,xres=1920,yres=1080 -cdrom "$ISO" -boot d \
  -display none -monitor unix:"$MON",server,nowait -daemonize -pidfile "$MON.pid"
sleep "$WAIT"
printf 'screendump %s.ppm\n' "$SHOT" | python3 -c "
import socket,sys,time
s=socket.socket(socket.AF_UNIX); s.connect('$MON'); time.sleep(0.5); s.recv(4096)
s.sendall(sys.stdin.read().encode()); time.sleep(2); s.sendall(b'quit\n'); time.sleep(0.5)"
python3 -c "from PIL import Image; Image.open('$SHOT.ppm').save('$SHOT')"; rm -f "$SHOT.ppm" "$VARS"
echo "saved $SHOT"
