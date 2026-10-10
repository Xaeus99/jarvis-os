# Install-day checklist (dual-boot next to Windows)

> ⚠️ **Draft. JARVIS OS is not released yet.** The installer has been tested in a virtual PC next to a dummy Windows
> disk (see the `install-test` workflow), never on real hardware. Wait for the release notes before you start.

Plan about **2 hours**. Nothing here deletes Windows. The only thing that gets erased is the USB stick.

## Weeks before: things to have
- [ ] **A USB stick, 16 GB or bigger** (32 GB is comfortable). Borrowing one is fine; it's only used once and gets erased.
- [ ] **A backup.** Best: a full *system image* on an external drive (Control Panel > Backup and Restore (Windows 7) >
      Create a system image). Free option: copy what you'd hate to lose (school files, clips, screenshots, game saves that
      aren't in the cloud) to your cloud storage. Steam games re-download for free.
- [ ] **Your Windows recovery key**, if the drive is encrypted: [aka.ms/myrecoverykey](https://aka.ms/myrecoverykey).
      Write it on paper or take a photo with your phone. Changing boot settings can make Windows ask for it once.

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
- [ ] Say **"Jarvis, restore my games"** to queue your Linux-compatible Steam games.

## If something goes wrong
- Windows is always one boot-menu key away: tap the boot-menu key and pick **Windows Boot Manager**.
- A bad JARVIS OS update: pick the previous version in the boot menu, or run `sudo bootc rollback`.
- Windows asks for a recovery key: it's the one you saved from aka.ms/myrecoverykey.
