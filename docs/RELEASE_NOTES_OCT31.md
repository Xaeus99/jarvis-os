# JARVIS OS 0.1 — release notes (draft for October 31)

> Draft. The final version gets the release tag, the download size and the checksums on release day.

## What it is

JARVIS OS is a gaming Linux that installs **next to Windows**. You keep Windows and all your games there. When
you turn on the PC, a menu lets you pick **JARVIS OS** or **Windows**. JARVIS is built in: you talk to it, it
talks back, and it runs on your own PC.

## The big stuff

- **Dual boot that leaves Windows alone.** Before the installer shows anything, a safety check finds Windows and
  **locks it** so nothing in the installer can erase, shrink or format it. In our test installs, Windows came out
  exactly the same, byte for byte.
- **JARVIS on the desktop.** Voice commands, the HUD ring, the hologram workshop (Earth, the suit, designs you
  describe), vitals, the 3D brain view, and the update countdown.
- **Gaming setup.** Steam, the NVIDIA driver for RTX cards, game mode, and "restore my games" to find the games
  you already have on Windows (it only reads Windows, never changes it).
- **Safe image checks.** Pictures JARVIS makes are checked for adult content and gore before you see them.
- **Updates that can't brick it.** Updates download in the background and switch over on the next restart. If an
  update is bad, pick "previous (rollback)" in the boot menu and you're back.
- **JARVIS look everywhere.** Purple Vibranium theme on the boot menu, the login screen, the desktop, and now the
  installer too.

## Installing it (short version)

1. **In Windows first:** Disk Management > right-click C: > Shrink Volume. Free up **100 GB or more**
   (40 GB is the minimum).
2. If your PC uses **BitLocker**, write down your recovery key first (aka.ms/myrecoverykey).
3. Download the installer from the Releases page with `get-jarvis-os.bat` (Windows) or `get-jarvis-os.sh`.
   It downloads the parts, joins them and checks them.
4. Put it on a USB stick (Rufus or Fedora Media Writer) and boot from it.
5. The **safety check** page comes first. Read it, then press **Enter**. Windows stays locked.
6. In the installer: **Installation Destination > Automatic > Done**, make your account, then
   **Begin Installation**. Never click "Reclaim space" or "Delete all".
7. Restart. On the first restart a blue **MOK** screen may show up: Enroll MOK > Continue > password
   `universalblue`. That lets the NVIDIA driver load with Secure Boot on.

The full step-by-step is in `docs/INSTALL_DAY.md`.

## What works with games

- Overwatch and Marvel Rivals work. Marvel Rivals needs the launch option `SteamDeck=1 %command%`.
- Terraria runs natively.
- **Rainbow Six Siege X does not work on Linux** (its anti-cheat blocks it). Play it on Windows.
- Anything else: check it before you count on it.

## Known limits

- Tested in virtual machines, not on a real PC yet. The first real install should be done carefully, with a
  backup of anything important.
- In the test machines there's no graphics card, so the hologram designs and voice race fall back to slower or
  simpler versions there. On a real NVIDIA card they should be fast, but that isn't proven yet.
- Some HYTE RGB parts aren't supported by OpenRGB yet.
- If your C: drive uses BitLocker, JARVIS OS can't read your Windows files unless you give it the recovery key.

## If something goes wrong

- Boot menu > **Windows** always gets you back to Windows.
- Boot menu > **previous (rollback)** undoes the last update.
- Say "take back control" or press **Meta+Shift+Esc** to stop anything JARVIS is doing.

## Also new (added Oct 14)

- **JARVIS boot splash.** When the PC starts, you see the JARVIS arc-reactor logo and a spinning purple ring instead of the stock logo.
- **JARVIS boot menu on the USB stick.** The installer stick opens on a JARVIS-themed menu. Pick **Install JARVIS OS** and press Enter.
- **Tested as a real USB stick.** The installer was copied byte for byte onto a (virtual) 16 GB stick, the way Rufus (DD mode) and Fedora Media Writer write it, and booted in both UEFI and older BIOS mode.
- **Your account, your way.** The installer's "User Creation" screen is where you type your name, user name and password. Leave "administrative privileges" ticked. Our tests now fill this screen in by hand, like you will, and then log in.


## Added Oct 14 (phase 10)
- **Install-day guide**: [docs/INSTALL_DAY_GUIDE.md](INSTALL_DAY_GUIDE.md), step by step: backup, BitLocker key,
  making space, writing the USB stick, the boot menu, every installer screen, and getting back to Windows.
- **USB sticks made with Rufus**: Rufus's default "ISO Image mode" (it copies files onto a FAT32 stick instead of an
  exact copy) is now tested in a VM too, next to the exact-copy way (Fedora Media Writer / Rufus DD mode).
  DD mode stays the recommended pick.
- **NVIDIA check**: a new automatic check reads, from inside each image, that the NVIDIA driver is set up to start
  early (so the boot splash and login screen use your RTX card from the start) and that nothing the base had is lost.
  If graphics ever fail, the screen falls back to plain mode instead of staying black.
- **RTX screen from the first second**: the image now turns on the NVIDIA driver's own screen handling
  (nvidia-drm.modeset=1) at boot, both as a boot setting and inside the early-boot files, instead of trusting the
  driver's default. Only your real card can show it working; the fallback to plain mode stays.
