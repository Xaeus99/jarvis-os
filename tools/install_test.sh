#!/bin/bash
# Dual-boot install test (CI, KVM). Proves the JARVIS OS installer only uses free space next to Windows.
#   install_test.sh prepare DISK.raw      -> GPT disk shaped like a real Windows 11 drive after "Shrink Volume":
#                                            ESP 100 MiB (Windows Boot Manager files), MSR 16 MiB, NTFS C: 24 GiB with
#                                            files, ~38 GiB unallocated, NTFS Recovery 750 MiB at the end
#   install_test.sh fingerprint DISK.raw OUT.txt -> sha256 of every Windows partition (raw bytes) + Windows boot files
#   install_test.sh install DISK.raw ISO OUTDIR  -> boots the ISO under OVMF, unattended install, screenshots
#   install_test.sh boot DISK.raw OUTDIR TAG     -> boots the installed disk, screenshots GRUB + login + desktop
#   install_test.sh collect DISK.raw OUTDIR      -> copies the in-VM report and installer logs off the disk
set -u
cmd=$1; shift
MON=/tmp/qmon.sock
mon() { echo "$1" | socat - UNIX-CONNECT:$MON >/dev/null 2>&1; }
shot() { mon "screendump /tmp/s.ppm"; sleep 0.5; convert /tmp/s.ppm "$OUT/$1.png" 2>/dev/null && echo "shot $1"; }
typekeys() { local s=$1 i c; for ((i=0; i<${#s}; i++)); do c=${s:i:1}; [ "$c" = "-" ] && c=minus; mon "sendkey $c"; sleep 0.15; done; }
alive() { kill -0 "$QPID" 2>/dev/null; }
loopup() { LOOP=$(sudo losetup -f --show -P "$1"); sudo partprobe "$LOOP" 2>/dev/null; sleep 1; }
loopdown() { sudo losetup -d "$LOOP"; }
qemu() { # qemu DISK [extra args...]
  local disk=$1; shift
  qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 4 -m 8192 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file=/tmp/vars.fd \
    -drive file="$disk",if=none,format=raw,id=hd,cache=unsafe -device virtio-blk-pci,drive=hd,bootindex=2 \
    -device virtio-vga -display none -nic user,model=virtio-net-pci \
    -monitor unix:$MON,server,nowait "$@" &
  QPID=$!; sleep 3; }

case $cmd in
prepare)
  D=$1; rm -f "$D"; truncate -s 64G "$D"
  sgdisk -o "$D" \
    -n1:2048:+100M -t1:ef00 -c1:"EFI system partition" \
    -n2:0:+16M -t2:0c01 -c2:"Microsoft reserved partition" \
    -n3:0:+24G -t3:0700 -c3:"Basic data partition" \
    -n4:$(( (64*1024*1024*1024 - 750*1024*1024) / 512 - 2048 )):0 -t4:2700 -c4:"Recovery" >/dev/null
  sgdisk -p "$D"
  loopup "$D"
  sudo mkfs.vfat -F32 -n SYSTEM "${LOOP}p1" >/dev/null
  sudo dd if=/dev/urandom of="${LOOP}p2" bs=1M count=16 status=none        # MSR has no filesystem; random bytes catch any write
  start() { sgdisk -i$1 "$D" | awk '/First sector/{print $3}'; }
  sudo mkntfs -Q -L Windows -p "$(start 3)" -H 255 -S 63 "${LOOP}p3" >/dev/null
  sudo mkntfs -Q -L Recovery -p "$(start 4)" -H 255 -S 63 "${LOOP}p4" >/dev/null
  # ESP content written with mtools (no vfat mount needed)
  T=$(mktemp -d); head -c 1536000 /dev/urandom > $T/bootmgfw.efi; head -c 32768 /dev/urandom > $T/BCD; head -c 32768 /dev/urandom > $T/RBCD
  E="sudo env MTOOLS_SKIP_CHECK=1"
  $E mmd -i "${LOOP}p1" ::/EFI ::/EFI/Microsoft ::/EFI/Microsoft/Boot ::/EFI/Microsoft/Recovery ::/EFI/Boot
  $E mcopy -i "${LOOP}p1" $T/bootmgfw.efi ::/EFI/Microsoft/Boot/bootmgfw.efi
  $E mcopy -i "${LOOP}p1" $T/BCD ::/EFI/Microsoft/Boot/BCD
  $E mcopy -i "${LOOP}p1" $T/RBCD ::/EFI/Microsoft/Recovery/BCD
  $E mcopy -i "${LOOP}p1" $T/bootmgfw.efi ::/EFI/Boot/bootx64.efi      # Windows' copy of its loader at the fallback path
  rm -rf $T; M=/tmp/m; sudo mkdir -p $M
  sudo mount -t ntfs-3g "${LOOP}p3" $M
  sudo mkdir -p $M/Windows/System32 $M/Users/Public/Documents "$M/Program Files/Steam/steamapps" $M/Users/Player/Pictures
  sudo dd if=/dev/urandom of=$M/Windows/System32/ntoskrnl.exe bs=1M count=12 status=none
  sudo dd if=/dev/urandom of=$M/pagefile.sys bs=1M count=256 status=none
  sudo dd if=/dev/urandom of=$M/Users/Player/Pictures/screenshot.png bs=1M count=8 status=none
  echo "If this file changes, the installer touched Windows." | sudo tee $M/Users/Public/Documents/canary.txt >/dev/null
  echo '"libraryfolders" { "0" { "path" "C:\\Program Files\\Steam" } }' | sudo tee "$M/Program Files/Steam/steamapps/libraryfolders.vdf" >/dev/null
  sudo umount $M
  sudo mount -t ntfs-3g "${LOOP}p4" $M; sudo mkdir -p $M/Recovery/WindowsRE
  sudo dd if=/dev/urandom of=$M/Recovery/WindowsRE/Winre.wim bs=1M count=300 status=none; sudo umount $M
  loopdown ;;
fingerprint)
  D=$1; F=$2; loopup "$D"; M=/tmp/m; sudo mkdir -p $M
  { echo "# partition table (Windows partitions 1-4 must keep start/end/type/GUID)"
    for n in 1 2 3 4; do sudo sgdisk -i$n "$D" | grep -E "GUID code|unique GUID|First sector|Last sector|Partition name"; done
    echo "# raw sha256 of the Windows partitions"
    for n in 2 3 4; do echo "p$n $(sudo sha256sum "${LOOP}p$n" | cut -d' ' -f1)"; done
    echo "# Windows boot files on the EFI system partition"
    T=$(mktemp -d); sudo env MTOOLS_SKIP_CHECK=1 mcopy -s -n -i "${LOOP}p1" ::/EFI "$T/" 2>/dev/null; sudo chown -R "$USER" "$T"
    (cd "$T" && find EFI -ipath 'EFI/Microsoft/*' -type f -exec sha256sum {} + | sort -k2)
    echo "# EFI/Boot fallback (shared path any OS may use)"
    (cd "$T" && find EFI -maxdepth 2 -ipath 'EFI/Boot/*' -type f -exec sha256sum {} + | sort -k2)
    echo "# everything on the ESP (info only)"; (cd "$T" && find . -type f | sort); rm -rf "$T"
  } > "$F"
  echo "# all partitions now (info)" >> "$F"; sudo sgdisk -p "$D" >> "$F"; sudo blkid "${LOOP}"p* >> "$F"
  loopdown; cat "$F" ;;
install)
  D=$1; ISO=$2; OUT=$3; mkdir -p "$OUT"; cp /usr/share/OVMF/OVMF_VARS_4M.fd /tmp/vars.fd
  qemu "$D" -drive file="$ISO",if=none,media=cdrom,id=cd,readonly=on -device ide-cd,drive=cd,bus=ide.1,bootindex=1 \
       -serial file:"$OUT/serial_install.log" -no-reboot
  shot i00_firmware; sleep 12; shot i01_iso_menu
  mon "sendkey up"; sleep 0.5; mon "sendkey ret"          # the "Install" entry (skip the media check)
  t=0; while alive && [ $t -lt 5400 ]; do sleep 60; t=$((t+60)); shot "$(printf 'i%02d_install_%04ds' $((t/60+1)) $t)"; done
  if alive; then echo "::error::installer still running after 90 min"; mon quit; exit 1; fi
  echo "installer powered the VM off after ~${t}s" ;;
boot)
  D=$1; OUT=$2; TAG=$3; mkdir -p "$OUT"
  qemu "$D" -serial file:"$OUT/serial_$TAG.log"
  for i in $(seq -w 0 14); do shot "${TAG}_a${i}_$((10#$i*2))s"; sleep 1.5; done     # firmware + GRUB menu
  for t in 1 2 3 4 5 6 7 8 9 10; do sleep 20; shot "${TAG}_b$(printf '%02d' $t)_$((30+t*20))s"; done
  mon "sendkey ret"; sleep 2; typekeys "jarvis-install-test"; mon "sendkey ret"
  for t in 1 2 3 4 5 6; do sleep 20; shot "${TAG}_c$(printf '%02d' $t)_desktop_$((t*20))s"; done
  sleep 30; mon system_powerdown; for i in $(seq 1 60); do alive || break; sleep 2; done; alive && mon quit; sleep 2 ;;
collect)
  D=$1; OUT=$2; loopup "$D"; M=/tmp/m; sudo mkdir -p $M
  for p in "${LOOP}"p*; do
    [ "$(sudo blkid -o value -s TYPE "$p")" = btrfs ] || continue
    sudo mount -o ro,subvolid=5 "$p" $M
    for f in $(sudo find $M -path '*/var/log/*' \( -name 'jarvis-install-report.txt' -o -name 'jarvis-*post.log' -o -path '*/anaconda/*' \) -type f 2>/dev/null); do
      sudo cp "$f" "$OUT/$(basename "$(dirname "$f")")__$(basename "$f")"; done
    sudo umount $M
  done
  sudo chown -R "$USER" "$OUT"; loopdown; ls -la "$OUT" ;;
esac
