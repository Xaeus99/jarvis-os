#!/bin/bash
# Click-through install test (CI, KVM): the NORMAL interactive JARVIS OS installer, driven the way a person would
# drive it, next to a disk shaped like a real Windows 11 drive after "Shrink Volume" (install_test.sh prepare).
#   clickthrough_test.sh MODE ISO DISK OUTDIR
# MODE = how the dual-boot safety page is answered:
#   enter    press ENTER (what the page tells you to do)            -> Windows must stay locked
#   timeout  type nothing; the page continues by itself after 120 s -> Windows must stay locked
#   unlock   type TOUCH WINDOWS, ENTER, YES, ENTER (advanced)       -> the Windows partitions are unlocked
# Then, in the installer's own window: Continue (language) > Installation Destination > Automatic (the default)
# > Done > Begin Installation, exactly as the safety page says; waits for "Complete!" and switches the VM off.
# Then User Creation, typed in the way he will do it (full name, user name, administrator, password twice); the
# test ISO's kickstart is the released one, unchanged (no pre-made user). The workflow then checks every Windows partition and boot file byte for byte, boots the new
# system once and reads the guard's log off the installed disk.
set -u
MODE=$1; ISO=$2; DISK=$3; OUT=$4; mkdir -p "$OUT"
here=$(cd "$(dirname "$0")" && pwd)
MON=/tmp/qmon-ct.sock; QMP=/tmp/qmp-ct.sock
mon() { echo "$1" | socat - UNIX-CONNECT:$MON >/dev/null 2>&1; }
N=0; LAST=""
shot() { N=$((N+1)); LAST=$(printf '%s/c%02d_%s.png' "$OUT" $N "$1"); mon "screendump /tmp/ct.ppm"; sleep 0.8
  convert /tmp/ct.ppm "$LAST" 2>/dev/null; tesseract "$LAST" - 2>/dev/null | tr '[:upper:]' '[:lower:]' > "${LAST%.png}.txt"; echo "shot $(basename "$LAST")"; }
has() { grep -Eq "$1" "${LAST%.png}.txt"; }
click() { python3 "$here/vmctl.py" $QMP clickword "$LAST" "$@"; }
key() { mon "sendkey $1"; sleep 0.25; }
typetext() { local s=$1 i c; for ((i=0; i<${#s}; i++)); do c=${s:i:1}
    case $c in " ") c=spc;; "-") c=minus;; ".") c=dot;; [A-Z]) c="shift-$(echo "$c" | tr A-Z a-z)";; esac; key "$c"; done; }
alive() { kill -0 "$QPID" 2>/dev/null; }
fail() { echo "FAIL ($MODE): $*" | tee "$OUT/verdict.txt"; echo "::error::$*"; shot fail; mon quit; sleep 2; exit 1; }
waitfor() { # waitfor REGEX SECONDS NAME -> polls the screen every 10 s
  local t=0; while alive && [ $t -lt $2 ]; do sleep 10; t=$((t+10)); shot "$3_${t}s"; has "$1" && return 0
    has "unknown error|an error occurred|traceback" && fail "installer error on screen ($(basename "$LAST"))"; done; return 1; }

# BOOT_MEDIA=cd (default: the ISO as a DVD) | usb (ISO is a USB stick's raw contents, e.g. a Rufus ISO-mode stick from
# tools/rufus_iso_mode_stick.sh, plugged in over USB 3 as removable mass storage; snapshot: the test never writes it)
case ${BOOT_MEDIA:-cd} in
  cd)  MEDIA=(-drive file="$ISO",if=none,media=cdrom,id=cd,readonly=on -device ide-cd,drive=cd,bus=ide.1,bootindex=1) ;;
  usb) MEDIA=(-device qemu-xhci,id=xhci -drive file="$ISO",if=none,format=raw,id=stick,snapshot=on
              -device usb-storage,bus=xhci.0,drive=stick,removable=on,bootindex=1) ;;
  *) echo "BOOT_MEDIA must be cd or usb"; exit 2 ;;
esac
startvm() { # startvm N: fresh VM (fresh firmware variables) booting the ISO's "Install" entry
  rm -f /tmp/vars.fd $MON $QMP; cp /usr/share/OVMF/OVMF_VARS_4M.fd /tmp/vars.fd
  qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 4 -m 8192 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file=/tmp/vars.fd \
    -drive file="$DISK",if=none,format=raw,id=hd,cache=unsafe -device virtio-blk-pci,drive=hd,bootindex=2 \
    "${MEDIA[@]}" \
    -device virtio-vga -display none -nic user,model=virtio-net-pci -usb -device usb-tablet \
    -monitor unix:$MON,server,nowait -qmp unix:$QMP,server,nowait -serial file:"$OUT/serial_$1.log" -no-reboot &
  QPID=$!; sleep 14; shot "a$1_boot_menu"
  key up; key ret; }                                       # "Install ..." (skips the media check)

# 1. the dual-boot safety page. The installer's own early boot has hung at "Switch Root" before anything runs
#    (install-test 38025874881, clickthrough 38326276668): then one retry in a fresh VM, as iso_boot_test.sh does.
#    Nothing has touched the disk at that point (the safety page itself comes first).
startvm 1
if ! waitfor "safety check" 600 boot; then
  echo "::warning::no safety page within 10 min (stuck at: $(tail -1 "${LAST%.png}.txt")); retrying in a fresh VM"
  mon quit; sleep 3; alive && kill "$QPID"; sleep 2
  startvm 2; waitfor "safety check" 600 boot2 || fail "the dual-boot safety page never came up (2 tries)"
fi
has "windows found" && has "locked" || fail "the safety page did not say Windows was found and locked"
cp "$LAST" "$OUT/safety_page.png"
case $MODE in
  enter)   key ret ;;
  timeout) echo "typing nothing; the page should continue by itself after 120 s" ;;
  unlock)  typetext "TOUCH WINDOWS"; key ret; sleep 2; shot unlock_question
           has "type yes" || fail "TOUCH WINDOWS did not ask for YES"
           typetext "YES"; key ret ;;
esac
T0=$(date +%s)

# 2. the installer's window: language page -> Continue (a kickstart that already names the user goes straight to the
#    summary, as in run 38321495176)
waitfor "what language|welcome to|installation summary" 420 lang || fail "the installer window never came up after the safety page"
[ "$MODE" = timeout ] && { W=$(( $(date +%s) - T0 )); echo "installer came up ${W}s after the safety page"; [ $W -ge 100 ] || fail "the page continued after only ${W}s with nothing typed"; }
cp "$LAST" "$OUT/installer_welcome.png"
if ! has "installation summary"; then key alt-c; sleep 5
  waitfor "installation summary" 120 hub || fail "no installation summary after Continue"; fi
cp "$LAST" "$OUT/installer_summary.png"

# 3b. User Creation, typed in like a person: the released kickstart makes no user, so the summary asks for one.
#     Full name, user name, administrator (wheel), password twice, Done (twice: the installer asks once more for a
#     weak password). The new system is then logged into with this password (install_test.sh boot + collect).
FULLNAME="Test Owner"; NEWUSER=owner; NEWPASS=jarvis-install-test
make_user() {
  click "user creation" || click "creation" || fail "could not find User Creation on the summary"
  waitfor "create user|full ?name|user ?name" 60 user || fail "User Creation did not open"
  cp "$LAST" "$OUT/user_creation_empty.png"
  # (OCR reads the labels as "Full name" or "fullname"; the Full name box already has the focus when the page opens)
  python3 "$here/vmctl.py" $QMP clickafter "$LAST" "full name" 60 || python3 "$here/vmctl.py" $QMP clickafter "$LAST" "fullname" 60 \
    || echo "Full name label not read; typing into the focused box"; sleep 0.5
  key ctrl-a; typetext "$FULLNAME"; sleep 1
  python3 "$here/vmctl.py" $QMP clickafter "$LAST" "user name" 60 || python3 "$here/vmctl.py" $QMP clickafter "$LAST" "username" 60 \
    || { echo "User name label not read; Tab to it"; key tab; }; sleep 0.5
  key ctrl-a; typetext "$NEWUSER"; sleep 1
  python3 "$here/vmctl.py" $QMP clickafter "$LAST" "password" 60 alone || fail "no Password box"; sleep 0.5
  typetext "$NEWPASS"; sleep 1
  python3 "$here/vmctl.py" $QMP clickafter "$LAST" "confirm password" 60 || fail "no Confirm password box"; sleep 0.5
  typetext "$NEWPASS"; sleep 2; shot user_filled; cp "$LAST" "$OUT/user_creation_filled.png"
  has "$NEWUSER" || fail "the user name did not show up in the box"
  click "done" || key alt-d; sleep 4; shot user_done1
  if ! has "installation summary"; then echo "still on User Creation (weak-password warning?): Done again"; click "done" || key alt-d; sleep 4; shot user_done2; fi
  waitfor "installation summary" 60 hub_user || fail "did not return to the summary after User Creation"; }
if has "user creation"; then
  make_user
  if ! has "administrator"; then   # not an admin yet: tick "Add administrative privileges" (its label toggles it)
    echo "user is not an administrator yet: ticking the box"
    click "user creation" || click "creation"; waitfor "create user|full ?name" 60 user2 || fail "User Creation did not reopen"
    click "administrative" || fail "no administrative-privileges box"; sleep 1; shot admin_ticked
    click "done" || key alt-d; sleep 4; shot admin_done1; has "installation summary" || { click "done" || key alt-d; sleep 4; }
    waitfor "installation summary" 60 hub_admin || fail "did not return to the summary after ticking administrator"
  fi
  has "administrator" || fail "the summary does not say an administrator account will be created"
  cp "$LAST" "$OUT/summary_user.png"
  echo "User Creation done by typing: $(grep -Eo '[a-z]* ?[a-z]+ will be created' "${LAST%.png}.txt" | head -1)"
else
  echo "::warning::no User Creation on the summary (a user was already set?)"
fi

# 3. Installation Destination > (disk ticked) > Automatic > Done
click "installation destination" || click "destination" || fail "could not find Installation Destination on the summary"
waitfor "storage configuration|local standard disks|device selection" 90 dest || fail "Installation Destination did not open"
cp "$LAST" "$OUT/destination.png"
has "automatic" || fail "no Automatic option on Installation Destination"
click "done" || key alt-d
sleep 6; shot after_done
# a tick may be needed (Anaconda only pre-ticks the disk in some versions): if we are still here, tick it and retry
if has "storage configuration|local standard disks" && ! has "installation summary"; then
  echo "still on Installation Destination: ticking the disk"; click "gib" || click "64"; sleep 2; shot ticked; click "done" || key alt-d; sleep 6; shot after_done2
fi
# never anything that frees space by deleting: if the installer asks, that is a failure (not enough free space seen)
has "reclaim space|delete all|installation options" && fail "the installer offered Reclaim space / Delete all (it did not see enough free space)"
waitfor "installation summary" 90 hub2 || fail "did not return to the summary after Done"
if has "no disks selected"; then   # the disk was not pre-ticked: tick it (as the safety page says) and Done again
  echo "no disk ticked: ticking it"; click "installation destination" || click "destination"
  waitfor "local standard disks|device selection" 90 dest2 || fail "Installation Destination did not open (2nd time)"
  click "gib" || fail "could not find the disk to tick"; sleep 2; shot ticked2; click "done" || key alt-d
  has "reclaim space|delete all|installation options" && fail "the installer offered Reclaim space / Delete all"
  waitfor "installation summary" 90 hub3 || fail "did not return to the summary after ticking the disk"
fi
has "no disks selected|error checking storage|not enough" && fail "storage not accepted: $(grep -E 'no disks|error|not enough' "${LAST%.png}.txt" | head -2)"
cp "$LAST" "$OUT/summary_ready.png"

# 4. Begin Installation, wait for Complete!
sleep 5; shot before_begin
click "begin installation" || click "begin" || key alt-b
waitfor "installation progress|deployment|setting up|configuring|running|installing|downloading|deploying" 120 started || fail "Begin Installation did not start the install"
t=0; ok=0
while alive && [ $t -lt 2700 ]; do sleep 30; t=$((t+30)); shot "install_${t}s"
  # (not "reboot system": that button is on the progress page from the start, greyed out)
  if has "complete!|successfully installed"; then ok=1; break; fi
  has "unknown error|an error occurred|traceback" && fail "installer error after ${t}s"; done
[ $ok = 1 ] || fail "no Complete! within 45 min"
cp "$LAST" "$OUT/install_complete.png"
# 5. Reboot System, as a person would: the installer unmounts the new system cleanly, then the VM stops (-no-reboot).
#    (Switching the VM off at "Complete!" lost the installer's last writes, its logs: run 38326276668.)
key alt-r                                    # the button's own shortcut (_Reboot System)
t2=0; while alive && [ $t2 -lt 300 ]; do sleep 5; t2=$((t2+5))
  if [ $t2 = 30 ] && alive; then   # OCR can't read white-on-blue: the button sits in the bottom-right corner
    read -r w h < <(python3 -c "import struct,sys; print(*struct.unpack('>II', open(sys.argv[1],'rb').read(24)[16:24]))" "$LAST")
    echo "clicking Reboot System at $((w-73)),$((h-30))"; python3 "$here/vmctl.py" $QMP click $((w-73)) $((h-30)) "$LAST"; fi
done
if alive; then echo "::warning::still running 5 min after Reboot System; powering off"; mon system_powerdown; sleep 60; alive && mon quit; sleep 3; fi
echo "PASS ($MODE): safety page answered ($MODE), Automatic > Done > Begin Installation, install complete after ~${t}s" | tee "$OUT/verdict.txt"
