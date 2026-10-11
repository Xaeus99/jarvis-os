#!/usr/bin/bash
# Local test for the dual-boot guard (needs sudo, loop devices, sgdisk, mkntfs, mtools, parted).
# Builds a small disk shaped like Windows after "Shrink Volume" and runs the guard on it with the page sent to
# stdout and the answer given on stdin. Checks: Windows found, partitions locked read-only, the ESP stays
# writable, the free space is named, TOUCH WINDOWS + YES unlocks, and the %post check sees no change.
set -u
here=$(cd "$(dirname "$0")/.." && pwd); G=$here/image/installer/dualboot-guard.sh; C=$here/image/installer/dualboot-guard-check.sh
D=$(mktemp /tmp/guarddisk.XXXX); truncate -s 3G "$D"
sgdisk -o -n1:2048:+100M -t1:ef00 -n2:0:+16M -t2:0c01 -n3:0:+1G -t3:0700 -n4:-300M:0 -t4:2700 "$D" >/dev/null
L=$(sudo losetup -f --show -P "$D"); sudo partprobe "$L" 2>/dev/null; sleep 1
start() { sgdisk -i$1 "$D" | awk '/First sector/{print $3}'; }
sudo mkfs.vfat -F32 "${L}p1" >/dev/null
sudo mkntfs -Q -F -L Windows -p "$(start 3)" -H 255 -S 63 "${L}p3" >/dev/null 2>&1; sudo mkntfs -Q -F -L Recovery -p "$(start 4)" -H 255 -S 63 "${L}p4" >/dev/null 2>&1
T=$(mktemp -d); echo x > $T/bootmgfw.efi
sudo env MTOOLS_SKIP_CHECK=1 mmd -i "${L}p1" ::/EFI ::/EFI/Microsoft ::/EFI/Microsoft/Boot
sudo env MTOOLS_SKIP_CHECK=1 mcopy -i "${L}p1" $T/bootmgfw.efi ::/EFI/Microsoft/Boot/bootmgfw.efi
M=$T/m; mkdir $M; sudo mount -t ntfs-3g "${L}p3" $M 2>/dev/null || sudo mount "${L}p3" $M; sudo mkdir -p $M/Windows/System32; sudo umount $M
fail=0; chk() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
run() { # run ANSWERS -> guard with page on stdout; only this loop disk is looked at
  printf "$1" > $T/in
  sudo env JARVIS_GUARD_TTY=stdout JARVIS_GUARD_WAIT=2 JARVIS_GUARD_INPUT=$T/in JARVIS_GUARD_LOG=$T/log JARVIS_GUARD_STATE=$T/st \
    JARVIS_GUARD_DISKS="$L" bash "$G" > $T/page 2>&1; sudo cat $T/log > $T/logcopy; }
run '\n'
cat $T/page
chk "Windows found" "grep -q 'Windows found' $T/page"
chk "C: recognised as the system drive" "grep -q 'Windows C: (system)' $T/page"
chk "Boot Manager seen on the EFI partition" "grep -q 'Windows Boot Manager' $T/page"
chk "C: locked" "[ \$(sudo blockdev --getro ${L}p3) = 1 ]"
chk "MSR locked" "[ \$(sudo blockdev --getro ${L}p2) = 1 ]"
chk "Recovery locked" "[ \$(sudo blockdev --getro ${L}p4) = 1 ]"
chk "EFI partition still writable" "[ \$(sudo blockdev --getro ${L}p1) = 0 ]"
chk "writing to C: is refused" "! sudo dd if=/dev/zero of=${L}p3 bs=512 count=1 conv=notrunc status=none 2>/dev/null"
chk "free space named" "grep -q 'GB free' $T/page"
chk "choice logged as locked" "sudo grep -q 'choice=locked' $T/log"
sudo env JARVIS_GUARD_LOG=$T/log JARVIS_GUARD_STATE=$T/st bash "$C"
chk "post-install check: unchanged" "sudo grep -q 'verdict: Windows partition entries unchanged' $T/log"
sudo sgdisk -d4 "$L" >/dev/null 2>&1; sudo env JARVIS_GUARD_LOG=$T/log JARVIS_GUARD_STATE=$T/st bash "$C"
chk "post-install check catches a removed Windows partition" "sudo grep -q 'verdict: Windows partition entries CHANGED' $T/log"
sudo blockdev --setrw "${L}p3" "${L}p2" 2>/dev/null; sudo losetup -d "$L"
sudo sfdisk "$D" < $T/st/$(basename "$L").before >/dev/null 2>&1 || sudo sfdisk --no-reread "$D" < <(sed "s#$L#$D#" $T/st/$(basename "$L").before) >/dev/null
L=$(sudo losetup -f --show -P "$D"); sudo partprobe "$L" 2>/dev/null; sleep 1
run 'TOUCH WINDOWS\nYES\n'
chk "TOUCH WINDOWS + YES unlocks" "sudo grep -q 'choice=unlocked' $T/log"
run 'TOUCH WINDOWS\nno\n'
chk "TOUCH WINDOWS without YES stays locked" "sudo grep -q 'choice=locked' $T/log"
sudo losetup -d "$L"; sudo rm -rf "$T" "$D"
[ $fail = 0 ] && echo "ALL GUARD CHECKS PASS" || { echo "GUARD CHECKS FAILED"; exit 1; }
