#!/bin/bash
# Puts the JARVIS look into the install media, in place:
#   tools/release/brand_iso.sh ISO
# 1. The installer window: adds images/product.img, the installer's own branding mechanism. Its early boot
#    (anaconda-dracut, anaconda_auto_updates) unpacks images/product.img over the installer's files BEFORE the window
#    starts. It carries image/installer/art/: the sidebar background, the JARVIS sidebar logo, the spoke top bar and
#    fedora.css (the installer's colour sheet with the JARVIS colour). A kickstart %pre can't do this: by then the
#    window has loaded its pictures.
# 2. The boot menu (UEFI and BIOS, from a DVD or a USB stick): the installed system's GRUB theme
#    (image/system_files/usr/share/grub/themes/jarvis) with installer words, in boot/grub2/themes/jarvis-installer,
#    and grub.cfg themed (tools/release/theme_grub_cfg.py: same entries, same order, same kernel lines) in all three
#    places a menu is read from: EFI/BOOT/grub.cfg, the same file inside images/efiboot.img (the UEFI boot image; it
#    is also the USB stick's EFI partition, the ISO's appended partition 2) and boot/grub2/grub.cfg (BIOS).
# Nothing else changes: same boot setup (xorriso -boot_image any replay, the way lorax's mkksiso edits ISOs), same
# volume label (the boot entries find the installer by it), same kickstart.
set -euo pipefail
ISO=$1; here=$(cd "$(dirname "$0")/../.." && pwd); ART=$here/image/installer/art
GTHEME=$here/image/system_files/usr/share/grub/themes/jarvis
command -v xorriso >/dev/null && command -v cpio >/dev/null && command -v mcopy >/dev/null || sudo apt-get install -y -qq xorriso cpio mtools >/dev/null
export MTOOLS_SKIP_CHECK=1
xorriso -indev "$ISO" -find /images -maxdepth 1 2>/dev/null | tee /tmp/iso-images.txt
if grep -q "/images/product.img" /tmp/iso-images.txt; then echo "::error::the ISO already has images/product.img; not overwriting it"; exit 1; fi
VOLID=$(xorriso -indev "$ISO" -pvd_info 2>/dev/null | sed -n 's/^Volume Id *: *//p')
[ -n "$VOLID" ] || { echo "::error::could not read the ISO's volume label"; exit 1; }
T=$(mktemp -d); P=$T/root/usr/share/anaconda/pixmaps; mkdir -p "$P"

# 1. product.img
for f in sidebar-bg.png sidebar-logo.png topbar-bg.png fedora.css; do cp "$ART/$f" "$P/$f"; done
(cd "$T/root" && find . | LC_ALL=C sort | cpio -o -H newc --quiet --owner=0:0 | gzip -9n) > "$T/product.img"
echo "product.img: $(stat -c %s "$T/product.img") bytes"; (cd "$T/root" && find . -type f)

# 2. boot menu
mkdir -p "$T/theme"; cp "$GTHEME"/* "$T/theme/"
sed -i -e 's/Good to see you, sir\. Choose a system\./Installer  ·  Windows stays locked unless you say otherwise/' \
       -e 's/Enter to boot  ·  Windows is never touched/Enter to start  ·  pick Install JARVIS OS/' "$T/theme/theme.txt"
grep -q "Installer  ·" "$T/theme/theme.txt" || { echo "::error::installer words not set in theme.txt"; exit 1; }
xorriso -osirrox on -indev "$ISO" -extract /EFI/BOOT/grub.cfg "$T/efi.cfg" -extract /boot/grub2/grub.cfg "$T/bios.cfg" \
  -extract /images/efiboot.img "$T/efiboot.img" 2>/dev/null
chmod u+w "$T"/*.cfg "$T/efiboot.img"
python3 "$here/tools/release/theme_grub_cfg.py" "$T/efi.cfg" "$T/efi.new"
python3 "$here/tools/release/theme_grub_cfg.py" "$T/bios.cfg" "$T/bios.new"
mtype -i "$T/efiboot.img" ::/EFI/BOOT/grub.cfg > "$T/efiimg.cfg"
python3 "$here/tools/release/theme_grub_cfg.py" "$T/efiimg.cfg" "$T/efiimg.new"
mcopy -o -i "$T/efiboot.img" "$T/efiimg.new" ::/EFI/BOOT/grub.cfg
mtype -i "$T/efiboot.img" ::/EFI/BOOT/grub.cfg | grep -q "JARVIS OS installer boot menu look" || { echo "::error::efiboot.img grub.cfg not themed"; exit 1; }
# the boot image's partition type as the ISO has it (GPT type GUID when the appended partition is in the GPT)
PTYPE=$(xorriso -indev "$ISO" -report_el_torito as_mkisofs 2>/dev/null | sed -n 's/^-append_partition 2 \([0-9a-fx]*\) .*/\1/p' | head -1)
[ -n "$PTYPE" ] || { echo "::error::no appended EFI partition found in the ISO (unexpected layout)"; exit 1; }
echo "appended partition 2 type: $PTYPE"

xorriso -indev "$ISO" -outdev "$ISO.branded" -boot_image any replay -volid "$VOLID" \
  -map "$T/product.img" /images/product.img -chmod 0444 /images/product.img -- \
  -map "$T/theme" /boot/grub2/themes/jarvis-installer -- \
  -map "$T/efi.new" /EFI/BOOT/grub.cfg -map "$T/bios.new" /boot/grub2/grub.cfg -map "$T/efiboot.img" /images/efiboot.img -- \
  -append_partition 2 "$PTYPE" "$T/efiboot.img" -end

# checks on the result
NEWVOL=$(xorriso -indev "$ISO.branded" -pvd_info 2>/dev/null | sed -n 's/^Volume Id *: *//p')
[ "$NEWVOL" = "$VOLID" ] || { echo "::error::volume label changed ($VOLID -> $NEWVOL)"; exit 1; }
xorriso -indev "$ISO.branded" -find /images/product.img 2>/dev/null | grep -q product.img || { echo "::error::product.img missing after the edit"; exit 1; }
xorriso -osirrox on -indev "$ISO.branded" -extract /EFI/BOOT/grub.cfg "$T/chk1" -extract /boot/grub2/grub.cfg "$T/chk2" \
  -extract /boot/grub2/themes/jarvis-installer/theme.txt "$T/chk3" 2>/dev/null
grep -q "JARVIS OS installer boot menu look" "$T/chk1" && grep -q "JARVIS OS installer boot menu look" "$T/chk2" && test -s "$T/chk3" \
  || { echo "::error::themed grub.cfg / theme missing after the edit"; exit 1; }
# the USB stick's EFI partition (appended partition 2) must carry the themed grub.cfg too
read -r PSTART PSIZE < <(xorriso -indev "$ISO.branded" -report_system_area plain 2>/dev/null | awk '/^GPT start and size : *2 /{print $(NF-1), $NF}')
dd if="$ISO.branded" of="$T/p2.img" bs=512 skip="$PSTART" count="$PSIZE" status=none
mtype -i "$T/p2.img" ::/EFI/BOOT/grub.cfg | grep -q "JARVIS OS installer boot menu look" || { echo "::error::the EFI partition on the stick is not themed"; exit 1; }
xorriso -indev "$ISO.branded" -report_el_torito as_mkisofs 2>/dev/null | grep -E '^(-b|-e|-append_partition|--grub2-mbr|-isohybrid|--protective)' || true
mv "$ISO.branded" "$ISO"; rm -rf "$T"
echo "branded: $ISO (volume $VOLID, images/product.img + JARVIS boot menu on UEFI, BIOS and the stick's EFI partition)"
