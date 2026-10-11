#!/usr/bin/bash
# JARVIS OS dual-boot guard, part 2 (kickstart %post --nochroot): did the Windows partition entries change?
# Compares each disk's partition table now with the copy dualboot-guard took before the install and writes the
# verdict to the guard log, which is then copied onto the installed system (/var/log/jarvis-dualboot-guard.log).
LOG=${JARVIS_GUARD_LOG:-/tmp/jarvis-dualboot-guard.log}; STATE=${JARVIS_GUARD_STATE:-/tmp/jarvis-guard}
ok=1
while read -r p disk; do
  [ -n "$p" ] || continue
  before=$(grep "^$p " "$STATE/$(basename "$disk").before" | sed 's/, *name=.*//')
  now=$(sfdisk -d "$disk" 2>/dev/null | grep "^$p " | sed 's/, *name=.*//')
  if [ -n "$before" ] && [ "$before" = "$now" ]; then echo "unchanged $p ro=$(blockdev --getro "$p" 2>/dev/null)" >> "$LOG"
  else ok=0; echo "CHANGED $p before='$before' now='$now'" >> "$LOG"; fi
done < "$STATE/windows-partitions"
echo "verdict: Windows partition entries $( [ $ok = 1 ] && echo unchanged || echo CHANGED)" >> "$LOG"
for d in "${ANA_INSTALL_PATH:-/mnt/sysimage}/var/log" /mnt/sysroot/ostree/deploy/default/var/log; do
  [ -d "$d" ] && cp "$LOG" "$d/jarvis-dualboot-guard.log"; done
exit 0
