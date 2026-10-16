#!/bin/bash
# OTA update test (CI, KVM): a JARVIS OS installed from an OLDER image receives the current signed update the way a
# real install does, boots it, can go back through the boot menu's "previous (rollback)" entry, and refuses an
# unsigned image. Uses tools/install_test.sh for the disk, the install and the first boot.
#   update_test.sh kickstart IN.toml OUT.toml   -> install-test kickstart + the in-VM recorder (jarvis-update-test.service)
#   update_test.sh bootphase DISK OUTDIR TAG PHASE [rollback]
#        boots the installed disk; the recorder inside reads PHASE (QEMU fw_cfg opt/jarvis/phase) and does it:
#          record   = log bootc status
#          upgrade  = log status, `bootc upgrade` (what the updater runs), log status
#          new      = log status, safe graphics entry vs booted entry, the voice commands' root helper as the user
#          final    = log status, try to switch to an UNSIGNED image of the same repo (must be refused)
#        "rollback" picks the boot menu's "previous (rollback)" entry (Down keys + OCR, Home, Down, Enter).
#        Waits for "JARVIS-UPDATE-TEST <PHASE> DONE" on the serial console, then shuts down cleanly (a staged update
#        is finalized at shutdown) and copies nothing: `install_test.sh collect` reads /var/log afterwards.
set -u
cmd=$1; shift
MON=/tmp/qmon.sock
mon() { echo "$1" | socat - UNIX-CONNECT:$MON >/dev/null 2>&1; }
shot() { mon "screendump /tmp/s.ppm"; sleep 0.5; convert /tmp/s.ppm "$OUT/$1.png" 2>/dev/null && echo "shot $1"; }
alive() { kill -0 "$QPID" 2>/dev/null; }

case $cmd in
kickstart)
  IN=$1; OUTF=$2
  python3 - "$IN" "$OUTF" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
t = open(src).read()
rec = r"""
%post --log=/var/log/jarvis-update-test-post.log
# what keeps a real install up to date (evidence only), then switch the automatic updaters off for this test so the
# test decides when the update happens
for u in uupd.timer bootc-fetch-apply-updates.timer rpm-ostreed-automatic.timer ublue-update.timer; do
  echo "updater $u: $(systemctl is-enabled $u 2>&1)"; done
cat /etc/rpm-ostreed.conf 2>/dev/null | grep -i -v '^#' | head -5
for u in uupd.timer bootc-fetch-apply-updates.timer rpm-ostreed-automatic.timer ublue-update.timer; do systemctl mask $u 2>/dev/null; done
cat > /etc/jarvis-update-test.sh <<'EOF'
#!/bin/bash
L=/var/log/jarvis-update-test.txt
say() { echo "JARVIS-UPDATE-TEST $*" | tee -a $L > /dev/ttyS0; }
modprobe qemu_fw_cfg 2>/dev/null
F=/sys/firmware/qemu_fw_cfg/by_name/opt/jarvis/phase/raw
if [ -r $F ]; then P=$(tr -dc 'a-z' < $F); else
  n=$(cat /var/lib/jarvis-update-test.count 2>/dev/null || echo 0); n=$((n+1)); echo $n > /var/lib/jarvis-update-test.count
  P=$(echo "record upgrade new record final" | cut -d' ' -f$n); fi
[ -n "$P" ] || P=record
st() { bootc status --format json 2>>$L | python3 -c '
import json,sys
s=json.load(sys.stdin).get("status",{})
def d(k):
    x=(s.get(k) or {}).get("image") or {}
    return "%s|%s|%s" % (((x.get("image") or {}).get("image")), x.get("version"), x.get("imageDigest"))
print("booted=%s staged=%s rollback=%s" % (d("booted"), d("staged"), d("rollback")))'; }
say "$P BEGIN $(date -Is) $(grep -o 'ostree=[^ ]*' /proc/cmdline)"
for i in $(seq 1 30); do curl -s -o /dev/null -m 10 https://ghcr.io/v2/ && break; sleep 5; done
say "$P STATUS-BEFORE $(st)"
case $P in
upgrade)
  ok=0
  for try in 1 2 3; do
    if bootc upgrade >> $L 2>&1; then ok=1; break; fi; say "upgrade try $try failed: $(tail -2 $L | tr '\n' ' ')"; sleep 30
  done
  say "upgrade rc-ok=$ok"
  say "$P STATUS-AFTER $(st)" ;;
new)
  B=$(grep -o 'ostree=[^ ]*' /proc/cmdline)
  C=$(grep -o 'ostree=[^ ]*' /boot/grub2/custom.cfg 2>/dev/null | head -1)
  say "safe-graphics booted=$B custom=$C title=$(grep -o "menuentry '[^']*'" /boot/grub2/custom.cfg 2>/dev/null | head -1)"
  [ -n "$C" ] && [ "$B" = "$C" ] && say "safe-graphics-matches-booted yes" || say "safe-graphics-matches-booted no"
  grep -H '^title' /boot/loader/entries/*.conf | sed 's/^/entry /' >> $L
  say "titles $(grep -h '^title' /boot/loader/entries/*.conf | tr '\n' ';')"
  # the voice commands' path: jarvisd runs as the person and calls the root helper through pkexec
  if [ -x /usr/libexec/jarvis-os/os-update ]; then
    out=$(runuser -u jarvis -- sh -c 'pkexec /usr/libexec/jarvis-os/os-update check' 2>&1); rc=$?
    say "helper-check rc=$rc out=$(echo "$out" | tr '\n' ' ' | head -c 300)"
    out=$(runuser -u jarvis -- sh -c 'bootc upgrade --check' 2>&1); rc=$?
    say "plain-bootc-as-user rc=$rc out=$(echo "$out" | tr '\n' ' ' | head -c 200)"
  else say "helper-check absent"; fi ;;
final)
  out=$(timeout 900 bootc switch "$(sed -n 's/^IMAGE_REPO=//p' /usr/lib/jarvis-os/version):update-test-unsigned" 2>&1); rc=$?
  say "unsigned-switch rc=$rc out=$(echo "$out" | tail -4 | tr '\n' ' ' | head -c 400)"
  say "$P STATUS-AFTER $(st)" ;;
esac
say "$P DONE"
EOF
chmod +x /etc/jarvis-update-test.sh
cat > /etc/systemd/system/jarvis-update-test.service <<'EOF'
[Unit]
Description=CI OTA update test recorder
Wants=network-online.target
After=network-online.target jarvis-safe-graphics.service jarvis-boot-names.service
[Service]
Type=oneshot
TimeoutStartSec=3000
ExecStart=/usr/bin/bash /etc/jarvis-update-test.sh
[Install]
WantedBy=multi-user.target
EOF
systemctl enable jarvis-update-test.service
%end
"""
Q = chr(39) * 3
i = t.rindex(Q)
open(dst, "w").write(t[:i] + rec.lstrip("\n") + t[i:])
PY
  grep -c "jarvis-update-test" "$OUTF" ;;
bootphase)
  D=$1; OUT=$2; TAG=$3; PHASE=$4; PICK=${5:-}; mkdir -p "$OUT"
  S="$OUT/serial_$TAG.log"
  qemu-system-x86_64 -enable-kvm -machine q35 -cpu host -smp 4 -m 8192 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file=/tmp/vars.fd \
    -drive file="$D",if=none,format=raw,id=hd,cache=unsafe -device virtio-blk-pci,drive=hd,bootindex=2 \
    -device virtio-vga -display none -nic user,model=virtio-net-pci \
    -fw_cfg name=opt/jarvis/phase,string="$PHASE" \
    -monitor unix:$MON,server,nowait -serial file:"$S" &
  QPID=$!; sleep 3
  if [ "$PICK" = rollback ]; then
    seen=0
    for i in $(seq 1 50); do
      mon "sendkey down"; sleep 0.2; mon "sendkey down"; sleep 0.2
      mon "screendump /tmp/s.ppm"; sleep 0.3
      convert /tmp/s.ppm "$OUT/${TAG}_menu_wait.png" 2>/dev/null || continue
      convert "$OUT/${TAG}_menu_wait.png" -resize 200% -colorspace Gray /tmp/s_big.png 2>/dev/null
      tesseract /tmp/s_big.png - 2>/dev/null | tr '[:upper:]' '[:lower:]' | tr -s ' \n' ' ' > /tmp/s_ocr.txt
      if grep -q "rollback" /tmp/s_ocr.txt; then seen=1; break; fi
    done
    if [ $seen = 0 ]; then echo "::error::boot menu with the previous (rollback) entry never showed ($(head -c 300 /tmp/s_ocr.txt))"; mon quit; sleep 2; exit 1; fi
    mon "sendkey home"; sleep 0.8; mon "sendkey down"; sleep 1.5; shot "${TAG}_a_menu_rollback_selected"
    convert "$OUT/${TAG}_a_menu_rollback_selected.png" -resize 200% -colorspace Gray /tmp/s_big.png
    tesseract /tmp/s_big.png - 2>/dev/null > "$OUT/${TAG}_a_menu_rollback_selected.txt"
    mon "sendkey ret"
  else
    for i in $(seq -w 0 9); do shot "${TAG}_a${i}"; sleep 1.5; done
  fi
  t=0; done=0
  while alive && [ $t -lt 2700 ]; do
    sleep 30; t=$((t+30))
    [ $((t % 120)) = 0 ] && shot "${TAG}_b$(printf '%04d' $t)s"
    if grep -aq "JARVIS-UPDATE-TEST $PHASE DONE" "$S"; then done=1; break; fi
  done
  grep -a "JARVIS-UPDATE-TEST" "$S" | cut -c1-400
  shot "${TAG}_c_done"
  [ $done = 1 ] || echo "::error::phase $PHASE did not finish within ${t}s"
  mon system_powerdown; for i in $(seq 1 300); do alive || break; sleep 2; done
  if alive; then echo "::error::VM did not shut down within 10 min"; mon quit; sleep 2; exit 1; fi
  echo "phase $PHASE: shut down cleanly after ~${t}s"
  [ $done = 1 ] ;;
esac
