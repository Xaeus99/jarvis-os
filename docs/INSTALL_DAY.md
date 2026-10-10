# Install-day checklist (dual-boot next to Windows)

> ⚠️ **Draft. JARVIS OS is not released yet.** The installer has been tested in a virtual PC next to a dummy Windows
> disk (see the `install-test` workflow), never on real hardware. Wait for the release notes before you start.

Plan about **2 hours**. Nothing here deletes Windows. The only thing that gets erased is the USB stick.

## Weeks before: things to have
- [ ] **A USB stick, 16 GB or bigger** (32 GB is comfortable). Borrowing one is fine; it's only used once and gets erased.
- [ ] **A backup.** Best: a full *system image* on an external drive (Control Panel > Backup and Restore (Windows 7) >
      Create a system image). Free option: copy what you'd hate to lose (school files, clips, screenshots, game saves that
      aren't in the cloud) to your cloud storage. Steam games re-download for free.
- [ ] **Is Windows encrypted (BitLocker / Device encryption)?** Check: Settings > Privacy & security > *Device
      encryption*, or in Terminal (Admin) `manage-bde -status C:`. Most new PCs with Windows 11 are.
- [ ] **If it is: get your recovery key** at [aka.ms/myrecoverykey](https://aka.ms/myrecoverykey) (48 digits, 8 groups
      of 6). Write it on paper or take a photo with your phone. You need it if Windows asks after the boot settings
      change, and it's one of the two ways JARVIS OS can link to Tab (below). **Don't turn BitLocker off** for this.

## Day of, in Windows (about 30 minutes)
- [ ] Install pending Windows updates and restart.
- [ ] **Turn off Fast Startup:** Control Panel > Power Options > *Choose what the power buttons do* > *Change settings
      that are currently unavailable* > untick *Turn on fast startup* > Save. (Windows keeps its drive half-locked otherwise.)
- [ ] **Make room:** right-click Start > **Disk Management** > right-click **C:** > **Shrink Volume**. Enter
      `153600` MB (150 GB). Afterwards C: must still have **at least 100 GB free**; shrink less if it wouldn't.
      Leave the new space **Unallocated**. Don't format it.
  - *Windows offers less than you asked for?* Open Terminal (Admin) and run `powercfg /h off`, turn off System
    Protection for C: for now, run Disk Cleanup, restart, try again. Turn them back on afterwards.
- [ ] **Make the USB stick:** download the JARVIS OS ISO from the release page, then write it with
      [Fedora Media Writer](https://fedoraproject.org/workstation/download) (*Select .iso file*) or Rufus (DD mode).

## Install (about 30–45 minutes)
- [ ] Restart and tap the **boot-menu key** (F11 on most ASRock and MSI boards, F12 on many others). Pick the
      **UEFI** entry for the USB stick. Leave Secure Boot **on**.
- [ ] Choose **Install JARVIS OS**.
- [ ] **Installation destination:** pick the disk, choose to install into the **free space only**. **Never** choose
      *erase*, *reclaim space* or *delete* anything. If the installer shows the Windows partitions, leave them alone.
- [ ] Create your user and password. Start the install and wait.

## First boot
- [ ] A **blue "MOK management" screen** appears once (Secure Boot key): *Enroll MOK* > *Continue* > *Yes* > password
      `universalblue` > *Reboot*. If you miss it, it comes back on the next boot.
- [ ] The JARVIS boot menu lists **JARVIS OS** and **Windows**. **Boot Windows once first** and check it's fine
      (enter the recovery key if it asks).
- [ ] Boot JARVIS OS, log in, connect to Wi-Fi. JARVIS installs the app list in the background and links itself to
      the same Tab as Windows JARVIS (`jarvisctl link --from-windows`, read-only).
      **If Windows is BitLocker-encrypted**, Linux can't read it on its own, so the automatic link says so and waits.
      Link it one of two ways (see *Linking to Tab when Windows is encrypted* below).
- [ ] Say **"Jarvis, restore my games"** to queue your Linux-compatible Steam games.

## Linking to Tab when Windows is encrypted (BitLocker)
Windows JARVIS keeps its Tab link (mailbox, reply channel and key) in its settings on C:. When C: is BitLocker-encrypted,
Linux sees only scrambled data. Two ways to link, both read-only for Windows:

1. **With the recovery key** (on the PC, 1 minute). Open Konsole and run
   `jarvisctl link --from-windows --bitlocker`. Type the 48-digit recovery key when asked, then your JARVIS OS password
   (the admin prompt). JARVIS opens C: **read-only**, copies only the Tab link, and closes it again. The key is used once
   and never saved. Nothing on Windows changes, and BitLocker stays on.
2. **With a link code from Tab** (no key needed, works even if Windows is gone). Text Tab "link code for JARVIS OS".
   Tab answers with a one-time code like `7KQ2-M9XD-R4TB`, good for one hour. Run
   `jarvisctl link --code 7KQ2-M9XD-R4TB`. The code itself never goes over the internet; it unlocks a sealed copy of
   the link settings that Tab posts for one hour.

Check it worked: say "Jarvis, ask Tab what time it is". If you hear an answer, you're linked.

## If something goes wrong
- Windows is always one boot-menu key away: tap the boot-menu key and pick **Windows Boot Manager**.
- A bad JARVIS OS update: pick **JARVIS OS … - previous (rollback)** in the boot menu, or run `sudo bootc rollback`.
- The firmware's own boot list (F11/F12) shows **JARVIS OS** and **Windows Boot Manager**. Right after installing it
  may say *Bazzite* for one boot; JARVIS OS renames it on its first start.
- Windows asks for a recovery key: it's the one you saved from aka.ms/myrecoverykey.
