#!/bin/bash
# Builds a USB stick image the way Rufus writes this ISO in its DEFAULT "ISO Image mode" (not DD mode):
# Rufus does NOT copy the ISO byte for byte there. It makes one partition, formats it, copies the ISO's FILES onto
# it, and patches the boot menu files so they point at the stick's new name. What Rufus 4.15 does, read from its
# own source (github.com/pbatard/rufus, src/format.c ToValidLabel, src/iso.c fix_config, src/rufus.c):
#  - file system: FAT32 when no file is over 4 GB (our biggest is ~0.9 GB, so FAT32 is Rufus's default here);
#    NTFS only if the person picks it, and then Rufus adds a small second "UEFI:NTFS" partition (res/uefi/uefi-ntfs.img)
#    that loads an NTFS driver and starts the stick's own EFI/BOOT/BOOTX64.EFI.
#  - name (label): FAT labels are upper-cased and cut to 11 characters, so the ISO's "Container-Installer-x86_64"
#    becomes "CONTAINER-I" (NTFS keeps up to 32 characters, so the name stays the same there).
#  - label patch: in every *.cfg file, on lines starting with options/append/linux/linuxefi/$linux/search/for, the ISO
#    name is replaced by the stick's name (so "inst.stage2=hd:LABEL=..." finds the stick). (The inst.stage2 ->
#    inst.repo rewrite is only for labels like Fedora-*-33..39 / RHEL-8/9, not ours.)
#  - partition scheme: MBR ("BIOS or UEFI", Rufus's default for an ISO that boots both ways) or GPT (UEFI only).
#   rufus_iso_mode_stick.sh ISO STICK VARIANT     VARIANT = fat32-mbr | fat32-gpt | fat32-nopatch | ntfs
#   fat32-nopatch = FAT32 but WITHOUT Rufus's label patch (what an older or different file-copy tool would make).
#   Writes STICK (a 16 GB raw image, STICK_SIZE to change) and prints what it did. Needs sudo, losetup, mkfs.vfat,
#   mkfs.ntfs (ntfs-3g), parted, curl (ntfs only).
set -euo pipefail
export PATH=$PATH:/usr/sbin:/sbin
ISO=$1; STICK=$2; VARIANT=$3; SIZE=${STICK_SIZE:-16G}
ISOLABEL=$(dd if="$ISO" bs=1 skip=32808 count=32 status=none | tr -d '\0' | sed 's/ *$//')   # ISO9660 volume id
[ -n "$ISOLABEL" ] || { echo "cannot read the ISO's label"; exit 1; }
rufus_fat_label() { python3 - "$1" <<'EOF'
import sys
s = sys.argv[1]; out = ""
for c in s:                                   # Rufus ToValidLabel(bFAT=TRUE)
    if c in '*?,;:/\\|+=<>[]"': continue
    if ord(c) >= 0x80: out += "_"; continue
    out += "_" if c in "\t." else c.upper()
out = out[:11]
print(out)
EOF
}
case $VARIANT in
  fat32-mbr|fat32-nopatch) FS=fat32; PT=msdos ;;
  fat32-gpt) FS=fat32; PT=gpt ;;
  ntfs) FS=ntfs; PT=msdos ;;
  *) echo "unknown variant $VARIANT"; exit 2 ;;
esac
if [ $FS = fat32 ]; then USBLABEL=$(rufus_fat_label "$ISOLABEL"); else USBLABEL=${ISOLABEL:0:32}; fi
echo "Rufus ISO mode ($VARIANT): ISO label '$ISOLABEL' -> stick label '$USBLABEL', $FS, partition table $PT"
rm -f "$STICK"; truncate -s "$SIZE" "$STICK"
if [ $FS = ntfs ]; then
  curl -fsSL -o /tmp/uefi-ntfs.img https://github.com/pbatard/rufus/raw/v4.15/res/uefi/uefi-ntfs.img
  NB=$(stat -c %s /tmp/uefi-ntfs.img); echo "UEFI:NTFS partition image from Rufus v4.15: $NB bytes, sha256 $(sha256sum /tmp/uefi-ntfs.img | cut -c1-16)..."
  END=$(( $(stat -c %s "$STICK") / 512 - NB / 512 - 1 ))
  parted -s "$STICK" mklabel msdos mkpart primary ntfs 1MiB "$((END))s" mkpart primary fat16 "$((END+1))s" 100% set 1 boot on
  sfdisk --part-type "$STICK" 1 7 >/dev/null; sfdisk --part-type "$STICK" 2 ef >/dev/null
elif [ $PT = msdos ]; then
  parted -s "$STICK" mklabel msdos mkpart primary fat32 1MiB 100% set 1 boot on set 1 lba on
else
  parted -s "$STICK" mklabel gpt mkpart "$USBLABEL" fat32 1MiB 100%
  sfdisk --part-type "$STICK" 1 EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 >/dev/null   # Microsoft basic data, as Rufus
fi
sfdisk -l "$STICK" | sed -n '/^Device/,$p'
# 1. the ISO's files (Rock Ridge names, as Rufus reads them with libcdio), read without mounting the ISO
STAGE=${STAGE:-$(dirname "$STICK")/rufus-stage-$VARIANT}; rm -rf "$STAGE"; mkdir -p "$STAGE"
NL=$(xorriso -indev "$ISO" -find / -type l 2>/dev/null | grep -c . || true)
BIG=$(xorriso -indev "$ISO" -find / -type f -size +4194303k 2>/dev/null | grep -c . || true)
NF=$(xorriso -indev "$ISO" -find / -type f 2>/dev/null | grep -c . || true)
echo "ISO tree: $NF files, $NL symlinks, $BIG files of 4 GB or more"
[ $FS = fat32 ] && [ "$BIG" -gt 0 ] && { echo "a file is over 4 GB: Rufus would not offer FAT32"; exit 1; }
xorriso -osirrox on -indev "$ISO" -extract / "$STAGE" 2>&1 | grep -Ei 'sorry|failure' | head -5 || true
chmod -R u+w "$STAGE"
# 2. Rufus's label patch on the *.cfg files (not for fat32-nopatch; nothing to do when the label didn't change)
if [ "$VARIANT" != fat32-nopatch ] && [ "$USBLABEL" != "$ISOLABEL" ]; then
  find "$STAGE" -iname '*.cfg' -print0 | while IFS= read -r -d '' f; do
    python3 - "$f" "$ISOLABEL" "$USBLABEL" "$STAGE" <<'EOF'
import sys
f, old, new, stage = sys.argv[1], sys.argv[2].replace(" ", "\\x20"), sys.argv[3].replace(" ", "\\x20"), sys.argv[4]
toks = ("options", "append", "linux", "linuxefi", "$linux", "search", "for")
lines = open(f, encoding="utf-8", errors="surrogateescape").read().split("\n"); n = 0
for i, l in enumerate(lines):
    w = l.strip().split(None, 1)
    if w and w[0].lower() in toks and old in l:
        lines[i] = l.replace(old, new); n += 1
if n:
    open(f, "w", encoding="utf-8", errors="surrogateescape").write("\n".join(lines))
    print(f"  Patched {f[len(stage):]}: '{old}' -> '{new}' ({n} lines)")
EOF
  done
fi
# 3. format the partition(s) and copy the files on
DEV=$(sudo losetup -Pf --show "$STICK"); sleep 1
trap 'sudo umount /tmp/rufus-usb 2>/dev/null || true; sudo losetup -d "$DEV" 2>/dev/null || true' EXIT
if [ $FS = fat32 ]; then sudo mkfs.vfat -F 32 -n "$USBLABEL" "${DEV}p1" >/dev/null
else sudo mkfs.ntfs -Q -L "$USBLABEL" -p 2048 -H 255 -S 63 "${DEV}p1" >/dev/null; sudo dd if=/tmp/uefi-ntfs.img of="${DEV}p2" bs=1M conv=fsync status=none; fi
sudo mkdir -p /tmp/rufus-usb
if [ $FS = fat32 ]; then MOPT=(-t vfat -o uid=$(id -u),gid=$(id -g),shortname=mixed); else MOPT=(-t ntfs-3g -o uid=$(id -u),gid=$(id -g)); fi
if sudo mount "${MOPT[@]}" "${DEV}p1" /tmp/rufus-usb 2>/dev/null; then
  cp -r --no-preserve=all "$STAGE"/. /tmp/rufus-usb/; sync
  echo "stick files: $(find /tmp/rufus-usb -type f | wc -l), used $(df -h --output=used /tmp/rufus-usb | tail -1 | tr -d ' ')"
  CHECK=/tmp/rufus-usb
elif [ $FS = fat32 ]; then   # no vfat in this kernel (a container): mtools writes the FAT file system directly
  sudo chmod a+rw "${DEV}p1"; (cd "$STAGE" && MTOOLS_SKIP_CHECK=1 mcopy -s -Q -i "${DEV}p1" ./* ::/)
  echo "stick files (mtools): $(MTOOLS_SKIP_CHECK=1 mdir -/ -b -i "${DEV}p1" :: | grep -vc '/$')"
  CHECK=$STAGE
else echo "cannot mount NTFS here"; exit 1; fi
grep -h 'inst.stage2' $CHECK/EFI/BOOT/grub.cfg | head -1 | sed 's/^\s*/EFI menu kernel line: /'
grep -h '^search' $CHECK/EFI/BOOT/grub.cfg | head -1 | sed 's/^/EFI menu search line: /'
sudo blkid "${DEV}p1" ${DEV}p2 2>/dev/null || true
sudo umount /tmp/rufus-usb 2>/dev/null || true; rm -rf "$STAGE"
