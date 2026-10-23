# JARVIS OS 1.0 "Vibranium" — release notes

> Release candidate: **v1.0.0-rc4** (pre-release, beta channel; rc1 Oct 16, rc2 and rc3 Oct 22, rc4 Oct 23: rc4 is rc3
> with the setup card's layout fixed). The final 1.0 gets the same notes plus its own
> tag, download size and checksums. Everything below was tested in virtual machines; what only your real PC can
> show is listed at the end.

## New in rc4 (Oct 23)

- **Setup card fits its text.** A setup line that also says what to do (for example "Say 'link to Tab'") could wrap
  onto a second line and run into the next item. Each line now takes the room its words need and the card grows to
  fit, so nothing overlaps. Same base as rc3 (Bazzite 44.20261012.1, Linux kernel 7.2.9: still the newest stable).

## New in rc2 and rc3 (Oct 22)

- **Newer base.** Rebuilt on this week's Bazzite (44.20261012.1, Linux kernel 7.2.9). Every test below ran again
  on the new image.
- **Graphics check.** At every login JARVIS checks that the NVIDIA driver is really running the RTX 5060. If not,
  you get a notification that says why in plain words (Secure Boot key not enrolled yet, the safe graphics entry,
  a driver that doesn't know the card) and what to do. Ask any time: "Jarvis, check my graphics card". "Fix the
  graphics driver" queues the Secure Boot key for you (it asks first).
- **A clearer first start.** The first login shows a **setup card**: graphics, apps, offline brain, microphone,
  Windows, Tab link, each marked done, still going, or needs you (with the words to say). "How's setup going?"
  reads it out.
- **Webcam features removed.** At your request, the "welcome back" camera greeting, "learn my face" and "forget my
  face" are gone, and any face data an older version saved is deleted when JARVIS starts. Nothing in JARVIS OS opens
  your webcam now. Asking for those old commands gets "That's been removed, sir."
- **Quest headset groundwork.** "Pair my headset" is understood and the pairing-code side is ready for the VR
  bridge; until the bridge lands it says so, and the JARVIS VR app pairs with JARVIS on Windows.
- **Cleaner builds.** The image's own quality check (bootc lint) had two warnings; both are fixed and any new one
  now stops the build. Build tools moved off a deprecated runtime.

## What it is

JARVIS OS is a gaming Linux that installs **next to Windows**. Windows and all your games there stay. When the PC
starts, a purple JARVIS menu lets you pick **JARVIS OS** or **Windows**. J.A.R.V.I.S. is built in: you talk to it,
it talks back, and it runs on your own PC.

## The big stuff

- **Dual boot that leaves Windows alone.** Before the installer shows anything, a safety check finds Windows and
  **locks it**, so nothing in the installer can erase, shrink or format it. In every test install (unattended,
  clicked through three ways, from a Rufus stick, and through updates and rollbacks) Windows came out byte for byte
  the same.
- **Works with Secure Boot on.** Tested on firmware set up like a Windows 11 PC (Secure Boot on, Microsoft keys):
  the installer starts, and on the first restart a blue **MOK** screen asks once for the password
  `universalblue`. After that JARVIS OS starts with Secure Boot still on and trusts the key that signs the NVIDIA
  driver. You don't have to turn Secure Boot off.
- **NVIDIA RTX 50 ready.** The open NVIDIA driver for RTX 50 cards (your 5060 is on its list), started early so
  the boot splash and login screen use the card, with `nvidia-drm.modeset=1` checked inside installed systems.
- **A safety net for the screen.** The last boot menu entry, **JARVIS OS - safe graphics**, starts with basic
  graphics if the screen ever stays black.
- **Updates that can't brick it.** Updates are signed. They download in the background and switch over on the
  next restart. The old version stays in the boot menu as **previous (rollback)**. Tested end to end: an install
  of an older release took the update, booted it, went back through "previous (rollback)", and came forward
  again. An unsigned image was refused.
- **Updates by voice.** "Jarvis, check for updates", "update JARVIS OS" and "roll back the update" now work
  (before this release they needed admin rights JARVIS didn't have).
- **JARVIS on the desktop.** Voice commands, the HUD ring, the hologram workshop, vitals, the 3D brain view, the
  update countdown, the offline brain, and Tab when you link it.
- **Gaming setup.** Steam, game mode, stream mode, "set up my monitor", and "restore my games" (reads your
  Windows Steam library, never changes it).
- **JARVIS look everywhere.** Vibranium purple on the boot menu, the splash, the installer, the login screen and
  the desktop.

## Installing it

Follow **[docs/INSTALL_DAY_GUIDE.md](INSTALL_DAY_GUIDE.md)** (the full walk-through) or the one-page
**[docs/INSTALL_DAY_CHECKLIST.md](INSTALL_DAY_CHECKLIST.md)**. Short version:

1. Back up. Save your **BitLocker recovery key** (aka.ms/myrecoverykey).
2. Windows Disk Management: shrink C: by **100 GB** (40 GB minimum). Leave the space empty.
3. Run `get-jarvis-os.bat` from the release page. It downloads, joins and checks the ISO.
4. **Rufus**: pick the stick and the ISO, keep **File system: FAT32**, START, then choose **DD Image mode**
   (safest; ISO Image mode was tested too). Don't pick NTFS.
5. Boot the stick (Settings > Recovery > Advanced startup > Use a device). Press **Enter** on the safety page.
6. Installer: **User Creation**, then **Installation Destination > Automatic > Done**, then **Begin Installation**.
7. First restart: blue **MOK** screen > Enroll MOK > Continue > Yes > `universalblue` > Reboot.

## What works with games

- Overwatch and Marvel Rivals work. Marvel Rivals needs the launch option `SteamDeck=1 %command%`.
- Terraria runs natively.
- **Rainbow Six Siege X does not work on Linux** (its anti-cheat blocks it). Play it on Windows.
- Anything else: check before you count on it.

## Tested for this release (all in virtual machines)

Unit tests (282), signed image build, boot to the desktop, NVIDIA boot settings, the NVIDIA driver loading with Secure Boot on, the voice test,
the installer ISO from DVD and USB stick (UEFI and old BIOS), Rufus ISO-mode sticks (FAT32 MBR and GPT), an
unattended dual-boot install, click-through installs (all three answers on the safety page, typed account, first
login, safe graphics boot), the Secure Boot + MOK install, and the update test (update, rollback, unsigned refused).

## Only your real PC can show

- The RTX 5060 actually drawing the splash, the login screen and games (the test machines have no NVIDIA card).
  The graphics check's RTX verdicts are tested with recorded system states; in the test VM it correctly reports
  that there is no NVIDIA card. Its first real "the 5060 is on the NVIDIA driver" will be on your PC.
- Your motherboard's own Secure Boot and boot menu (tested on standard virtual firmware with Microsoft's keys).
- A real USB stick written by real Rufus (we copy exactly what Rufus 4.15 does).
- BitLocker on your real C: drive, your real disk sizes, NVMe.
- Microphone and wake word in your room, HYTE RGB, speakers and Bluetooth.

## Known limits

- Some HYTE RGB parts aren't supported by OpenRGB yet.
- If C: uses BitLocker, JARVIS OS can't read your Windows files without the recovery key.
- Updates download in the background by themselves (Bazzite's updater is on). They never restart the PC for you.
- The release candidate follows the **beta** channel (test builds). The final 1.0 follows **stable**. Moving an
  RC install to stable is one command (`pkexec /usr/libexec/jarvis-os/os-update switch stable`), or Tab does it.

## If something goes wrong

- Boot menu > **Windows Boot Manager** always gets you back to Windows.
- Boot menu > **previous (rollback)** undoes the last update. Or say "roll back the update".
- Boot menu > **JARVIS OS - safe graphics** if the screen stays black.
- Say "take back control" or press **Meta+Shift+Esc** to stop anything JARVIS is doing.
