#!/bin/bash
# Puts the JARVIS look into the installer: adds images/product.img to a bootc-image-builder installer ISO, in place.
#   tools/release/brand_iso.sh ISO
# product.img is the installer's own branding mechanism: its early boot (anaconda-dracut, anaconda_auto_updates)
# unpacks images/product.img from the install media over the installer's files BEFORE the installer window starts.
# It carries image/installer/art/: the sidebar background, the JARVIS sidebar logo, the spoke top bar and fedora.css
# (the installer's colour sheet with the JARVIS colour). Nothing else in the ISO changes: same boot setup (xorriso
# -boot_image any replay, the way lorax's mkksiso edits ISOs), same volume label (the boot entries find the
# installer by it), same kickstart. A kickstart %pre can't do this: by then the window has loaded its pictures.
set -euo pipefail
ISO=$1; here=$(cd "$(dirname "$0")/../.." && pwd); ART=$here/image/installer/art
command -v xorriso >/dev/null && command -v cpio >/dev/null || sudo apt-get install -y -qq xorriso cpio >/dev/null
xorriso -indev "$ISO" -find /images -maxdepth 1 2>/dev/null | tee /tmp/iso-images.txt
if grep -q "/images/product.img" /tmp/iso-images.txt; then echo "::error::the ISO already has images/product.img; not overwriting it"; exit 1; fi
VOLID=$(xorriso -indev "$ISO" -pvd_info 2>/dev/null | sed -n 's/^Volume Id *: *//p')
[ -n "$VOLID" ] || { echo "::error::could not read the ISO's volume label"; exit 1; }
T=$(mktemp -d); P=$T/root/usr/share/anaconda/pixmaps; mkdir -p "$P"
for f in sidebar-bg.png sidebar-logo.png topbar-bg.png fedora.css; do cp "$ART/$f" "$P/$f"; done
(cd "$T/root" && find . | LC_ALL=C sort | cpio -o -H newc --quiet --owner=0:0 | gzip -9n) > "$T/product.img"
echo "product.img: $(stat -c %s "$T/product.img") bytes"; (cd "$T/root" && find . -type f)
xorriso -indev "$ISO" -outdev "$ISO.branded" -boot_image any replay -volid "$VOLID" \
  -map "$T/product.img" /images/product.img -chmod 0444 /images/product.img -- -end
NEWVOL=$(xorriso -indev "$ISO.branded" -pvd_info 2>/dev/null | sed -n 's/^Volume Id *: *//p')
[ "$NEWVOL" = "$VOLID" ] || { echo "::error::volume label changed ($VOLID -> $NEWVOL)"; exit 1; }
xorriso -indev "$ISO.branded" -find /images/product.img 2>/dev/null | grep -q product.img || { echo "::error::product.img missing after the edit"; exit 1; }
mv "$ISO.branded" "$ISO"; rm -rf "$T"
echo "branded: $ISO (volume $VOLID, images/product.img added)"
