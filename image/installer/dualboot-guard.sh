#!/usr/bin/bash
# JARVIS OS dual-boot guard. Runs inside the installer (kickstart %pre) BEFORE any disk is touched.
#  1. finds Windows on every disk (Windows Boot Manager on an EFI partition, an NTFS or BitLocker C:, the
#     Microsoft reserved and Windows recovery partitions) and the free space next to it;
#  2. LOCKS every Windows partition read-only at the kernel level (blockdev --setro) for the whole install, so no
#     installer screen can format, resize or write to it. The shared EFI partition stays writable (the boot menu
#     goes there, Windows' own files on it are never changed);
#  3. shows a full-screen page in plain words: what was found, where JARVIS OS will go, what to click next;
#  4. unlocks only if the person types TOUCH WINDOWS and then YES (advanced partitioning). Enter, or no answer
#     within JARVIS_GUARD_WAIT seconds (unattended installs), keeps Windows locked.
# It also saves each disk's partition table, and %post (dualboot-guard-check) compares the Windows entries after.
# Source of truth: image/installer/dualboot-guard.sh; tools/sync_installer_guard.py copies it into iso.toml and
# install-test.toml. Test locally: tools/test_dualboot_guard.sh (needs sudo + loop devices).
LOG=${JARVIS_GUARD_LOG:-/tmp/jarvis-dualboot-guard.log}
STATE=${JARVIS_GUARD_STATE:-/tmp/jarvis-guard}
TTYN=${JARVIS_GUARD_TTY:-8}
WAIT=${JARVIS_GUARD_WAIT:-120}
MINGB=40; RECGB=100
mkdir -p "$STATE"; : > "$LOG"; : > "$STATE/windows-partitions"
log() { echo "$*" >> "$LOG"; }
MSR=e3c9e316-0b5c-4db8-817d-f92df00215ae; WINRE=de94bba4-06d1-4d40-a16a-bfd50179d6ac
gb() { echo $(( ($1 + 500000000) / 1000000000 )); }
hs() { if [ "$1" -ge 1000000000 ]; then echo "$(gb "$1") GB"; else echo "$(( ($1 + 500000) / 1000000 )) MB"; fi; }
haswbm() { # $1 = a vfat partition: is Windows Boot Manager on it? read-only mount, or mtools if mounting isn't possible
  local m r=1; m=$(mktemp -d)
  if mount -o ro "$1" "$m" 2>/dev/null; then ls "$m"/[Ee][Ff][Ii]/[Mm]icrosoft/[Bb]oot/bootmgfw.efi >/dev/null 2>&1 && r=0; umount "$m"
  elif command -v mdir >/dev/null; then MTOOLS_SKIP_CHECK=1 mdir -b -i "$1" ::/EFI/Microsoft/Boot 2>/dev/null | grep -qi bootmgfw.efi && r=0; fi
  rmdir "$m"; return $r; }
log "=== JARVIS OS dual-boot guard $(date -Is 2>/dev/null)"
WINPARTS=(); LINES=(); FREE_LINES=(); BITLOCKER=0; ESPWIN=0; BEST_FREE=0; BEST_DISK=""
for disk in ${JARVIS_GUARD_DISKS:-$(lsblk -dnpo NAME,TYPE,RO | awk '$2=="disk" && $3=="0" {print $1}')}; do
  [ -z "${JARVIS_GUARD_DISKS:-}" ] && case $disk in /dev/zram*|/dev/loop*|/dev/sr*) continue;; esac
  model=$(lsblk -dno MODEL "$disk" 2>/dev/null | sed 's/ *$//'); dsize=$(lsblk -dnbo SIZE "$disk")
  sfdisk -d "$disk" > "$STATE/$(basename "$disk").before" 2>/dev/null
  found=0
  while read -r name size fstype ptype; do
    [ "$name" = "$disk" ] && continue
    # probe the partition directly (works even before udev has filled in its cache)
    [ "$fstype" = - ] && fstype=$(blkid -p -o value -s TYPE "$name" 2>/dev/null)
    [ "$ptype" = - ] && ptype=$(blkid -p -o value -s PART_ENTRY_TYPE "$name" 2>/dev/null)
    role=""
    case "${fstype,,}" in
      ntfs) role="Windows (NTFS)";;
      bitlocker) role="Windows (BitLocker)"; BITLOCKER=1;;
    esac
    [ "${ptype,,}" = "$MSR" ] && role="Microsoft reserved"
    [ "${ptype,,}" = "$WINRE" ] && role="Windows recovery"
    if [ "${fstype,,}" = vfat ] && haswbm "$name"; then ESPWIN=1; found=1
      LINES+=("  $(printf '%-13s %7s  %s' "${name#/dev/}" "$(hs "$size")" "EFI boot (Windows Boot Manager)") -> shared, kept")
      log "esp $name has Windows Boot Manager"; fi
    [ -z "$role" ] && continue
    # is this NTFS partition the Windows system drive? (read-only look for Windows\System32)
    if [ "${fstype,,}" = ntfs ]; then m=$(mktemp -d)
      if mount -t ntfs3 -o ro "$name" "$m" 2>/dev/null || mount -t ntfs-3g -o ro "$name" "$m" 2>/dev/null || mount -o ro "$name" "$m" 2>/dev/null; then
        ls -d "$m"/[Ww]indows/[Ss]ystem32 >/dev/null 2>&1 && role="Windows C: (system)"; umount "$m"; fi; rmdir "$m"; fi
    found=1; WINPARTS+=("$name"); echo "$name $disk" >> "$STATE/windows-partitions"
    LINES+=("  $(printf '%-13s %7s  %s' "${name#/dev/}" "$(hs "$size")" "$role") -> LOCKED")
    log "windows $name size=$size fstype=$fstype parttype=$ptype role=$role"
  done < <(lsblk -rnbpo NAME,SIZE,FSTYPE,PARTTYPE "$disk" | awk '{print $1, $2, ($3==""?"-":$3), ($4==""?"-":$4)}')
  # largest free gap on this disk
  free=$(parted -s -m "$disk" unit B print free 2>/dev/null | awk -F: '$5=="free;" {gsub("B","",$4); if ($4>m) m=$4} END {print m+0}')
  log "disk $disk model=$model size=$dsize windows=$found largest_free=$free"
  if [ "$found" = 1 ] || [ "$free" -gt 1000000000 ]; then
    FREE_LINES+=("  ${disk#/dev/} ${model:+($model) }$(gb "$dsize") GB disk: $(gb "$free") GB free"); fi
  if [ "$free" -gt "$BEST_FREE" ]; then BEST_FREE=$free; BEST_DISK=${disk#/dev/}; fi
done
NWIN=${#WINPARTS[@]}
# lock first, ask second: Windows is protected from the very first moment
for p in "${WINPARTS[@]}"; do blockdev --setro "$p" && log "locked $p ro=$(blockdev --getro "$p")"; done
page() {
  echo; echo "  J.A.R.V.I.S. OS INSTALLER  -  DUAL-BOOT SAFETY CHECK"; echo "  ===================================================="; echo
  if [ "$NWIN" -gt 0 ] || [ "$ESPWIN" = 1 ]; then
    echo "  Windows found on this PC:"; printf '%s\n' "${LINES[@]}"; echo
    echo "  Windows stays exactly as it is. Its partitions are LOCKED read-only for this"
    echo "  whole install, so nothing on the next screens can format, resize or erase them."
  else
    echo "  No Windows install was found on the disks this installer can see."
    echo "  Expected one? Power off now (hold the power button). Windows may be on a disk"
    echo "  hidden by RAID/Intel RST mode (set SATA mode to AHCI in the BIOS)."
  fi
  echo; echo "  Free space:"; printf '%s\n' "${FREE_LINES[@]:-  (no disks with free space)}"
  if [ "$(gb "$BEST_FREE")" -ge "$MINGB" ]; then
    echo "  -> JARVIS OS goes into the free space on $BEST_DISK$( [ "$(gb "$BEST_FREE")" -lt "$RECGB" ] && echo " ($RECGB+ GB is better for games)")."
    echo; echo "  NEXT SCREEN: Installation Destination > tick $BEST_DISK > Storage Configuration:"
    echo "  Automatic > Done. Automatic uses ONLY the free space."
    echo "  Never pick \"Reclaim space\" or \"Delete all\"."
  else
    echo "  -> Not enough free space (JARVIS OS needs $MINGB GB, largest gap $(gb "$BEST_FREE") GB)."
    echo "     Nothing has been changed. In Windows: Disk Management > right-click C: >"
    echo "     Shrink Volume (100 GB or more), then start this installer again."
  fi
  if [ "$BITLOCKER" = 1 ]; then echo
    echo "  BitLocker is ON. Have your recovery key ready (aka.ms/myrecoverykey): Windows"
    echo "  may ask for it once after the first restart, because the boot menu changed."
  fi
  echo; echo "  ENTER = continue with Windows locked (continues by itself in ${WAIT}s)."
  [ "$NWIN" -gt 0 ] && echo "  Advanced only: type  TOUCH WINDOWS  and ENTER to unlock the Windows partitions."
}
TTY=/dev/tty$TTYN; ORIG=""
if [ "$TTYN" = stdout ]; then exec 3>&1; TTY=/dev/stdin
else ORIG=$(fgconsole 2>/dev/null); exec 3>"$TTY"; chvt "$TTYN" 2>/dev/null; printf '\033[2J\033[H' >&3; fi
page >&3 2>&1; page >> "$LOG"
exec 4<"${JARVIS_GUARD_INPUT:-$TTY}"
ans=""; read -r -t "$WAIT" ans <&4 2>/dev/null
CHOICE=locked
if [ "$NWIN" -gt 0 ] && [ "$ans" = "TOUCH WINDOWS" ]; then
  { echo; echo "  Unlocking lets the next screens change Windows partitions. A wrong click there can"
    echo "  ERASE Windows and your games. Type YES to unlock, anything else keeps Windows locked:"; } >&3
  ans2=""; read -r -t "$WAIT" ans2 <&4 2>/dev/null
  if [ "$ans2" = YES ]; then CHOICE=unlocked; for p in "${WINPARTS[@]}"; do blockdev --setrw "$p" && log "unlocked $p"; done; fi
fi
log "choice=$CHOICE windows_partitions=$NWIN esp_windows=$ESPWIN bitlocker=$BITLOCKER best_free=$BEST_FREE on $BEST_DISK answer=$( [ -n "$ans" ] && echo given || echo none)"
echo "  Windows: $CHOICE. Starting the installer..." >&3; sleep 2
[ -n "$ORIG" ] && chvt "$ORIG" 2>/dev/null
exit 0
