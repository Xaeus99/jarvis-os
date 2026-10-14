# Install day: JARVIS OS next to Windows, step by step

About 1 to 1.5 hours. Windows stays on your PC. You pick JARVIS OS or Windows every time the PC starts.
Do the steps in order. If something on screen doesn't match this guide, **stop and text JARVIS/Tab a photo of the screen**.

You need: your PC, a USB stick of **16 GB or more** (it gets wiped), another phone or device to read this on,
and about 100 GB of free space on your main drive (at least 40 GB).

---

## 1. Back up what matters (15 min)

Nothing in this guide deletes Windows, but back up anyway. It's the one step you can't redo later.

- Copy these to a USB drive, external drive or OneDrive: **Documents, Pictures, Videos, Desktop**, game clips,
  OBS scenes/settings, and any mods or save files that aren't in the cloud.
- Steam, Epic and Battle.net games re-download, so you don't need to copy them.
- Check the copy opens on another device before you move on.

## 2. BitLocker: get your recovery key (5 min)

Windows may have locked (encrypted) your C: drive with BitLocker. Changing boot settings can make Windows ask
for a 48-digit **recovery key** once. Without it you'd be locked out of Windows, so get it now.

1. Press **Start**, type **Manage BitLocker**, open it. (On Windows Home, look in **Settings > Privacy & security
   > Device encryption**.)
2. If C: says **BitLocker on** (or Device encryption is On): click **Back up your recovery key** and save it to
   your Microsoft account, **and** take a photo of it or write it down.
3. You can also see it any time at **aka.ms/myrecoverykey** (sign in with your Microsoft account) on your phone.
4. If it says **off**, you're fine. Move on.

Keep the key on your phone, not just on the PC.

## 3. Make space in Windows (10 min)

1. Right-click **Start** > **Disk Management**.
2. Right-click your **C:** drive > **Shrink Volume...**
3. In "Enter the amount of space to shrink in MB" type **102400** (that's 100 GB; the least is 40960 = 40 GB).
4. Click **Shrink**. A black bar called **Unallocated** appears next to C:. **Leave it empty.** Don't make a new
   volume in it. That empty space is where JARVIS OS goes.

If Shrink offers much less than you asked, run Disk Cleanup, empty the Recycle Bin, restart, and try again.

## 4. Write the USB stick (20 min)

1. Download the installer from the latest release:
   the repo's **Releases** page ([latest release](../../../releases/latest)). The easy way: download **get-jarvis-os.bat** and double-click
   it. It downloads the 4 parts, joins them into one `.iso` file and checks it. (Or download the 4 `.iso.part`
   files yourself and follow the release page.)
2. Write the `.iso` to the stick with **one** of these:
   - **Fedora Media Writer** (easiest): pick *Select .iso file*, choose the JARVIS file, pick your stick, *Write*.
   - **Rufus**: pick your stick, click *SELECT* and choose the JARVIS `.iso`, leave the rest as it is, click *START*.
     When Rufus asks **"ISOHybrid image detected"**, either choice works:
     **"Write in DD Image mode"** is the safest (it copies the stick exactly, like Fedora Media Writer), and
     Rufus's default **"Write in ISO Image mode"** was tested too: it booted and installed fine. If you're unsure,
     pick DD mode, then OK.
3. It takes 5-15 minutes. Windows may say the stick "needs to be formatted" afterwards: click **Cancel**. That's
   normal, Windows just can't read it.

## 5. Start the PC from the stick (5 min)

The simplest way, works on any modern PC:

1. Plug the stick into a USB port on the back of the PC (straight into the motherboard).
2. In Windows: **Settings > System > Recovery > Advanced startup > Restart now**.
3. On the blue screen: **Use a device** > pick the USB stick (it may be called "UEFI: USB..." or the stick's brand).

If you don't see "Use a device": restart and tap the **boot menu key** as soon as the screen lights up:
ASUS **F8**, MSI **F11**, Gigabyte **F12**, ASRock **F11**, prebuilt HP **F9**, Dell/Lenovo **F12**. Pick the entry
that starts with **UEFI:** and has your stick's name.

If the PC says "Secure Boot violation" or won't start the stick, text JARVIS/Tab a photo. Don't change other
BIOS settings on your own.

## 6. The installer, screen by screen (30-40 min)

**a. The JARVIS boot menu** (purple, arc reactor). "Install JARVIS OS" is already picked. Press **Enter** (or wait).

**b. Black screen with text for 1-2 minutes.** Normal, it's loading. On an NVIDIA card it can stay black
for up to a minute; wait 3 minutes before worrying. If it never shows a picture, restart, go back to the boot
menu and pick **Troubleshooting > Install JARVIS OS in basic graphics mode**.

**c. "JARVIS OS - Windows safety check".** It lists your Windows parts as **LOCKED**. That means the installer
*can't* touch Windows. Press **Enter**. (Don't type anything else. If you do nothing, it continues by itself
after 2 minutes, still locked.)

**d. Welcome / language.** Pick **English (United States)** (or yours) > **Continue**.

**e. Installation Summary** (a page of icons). You do 2 things here:

1. **User Creation**: type your name, a user name (lowercase, no spaces), tick **Make this user administrator**
   (it's ticked already), type a password twice. Remember it! Click **Done** (top left). Click Done twice if it
   says the password is weak.
2. **Installation Destination**: your drive is already ticked, and **Automatic** is already picked. Click
   **Done** (top left). That's it. It uses only the empty Unallocated space you made.
   - **Never** click "Reclaim space" or "Delete all". (They won't work anyway, Windows is locked, but don't.)
   - If it says there isn't enough free space, click Cancel, quit, and go back to step 3.

**f. Begin Installation** (bottom right, turns blue). Click it. It takes 10-20 minutes. Don't turn the PC off.

**g. "Complete!"** Click **Reboot System**. Pull the USB stick out when the screen goes black.

**h. Blue screen "Perform MOK management"** (only once, needed for the NVIDIA driver with Secure Boot):
   **Enroll MOK** > **Continue** > **Yes** > type the password **universalblue** (you won't see letters
   appear) > Enter > **Reboot**. If you miss it (it waits, but not forever) the PC still starts; JARVIS OS just
   uses basic graphics until you redo it (text JARVIS/Tab, we'll do it together).

**i. The JARVIS menu**: **JARVIS OS** is first, then **Windows Boot Manager**. Pick JARVIS OS. You'll see the
arc reactor splash, then the login screen. Log in with the password from step e. Done!

## 7. Getting back to Windows

- **Every start**: pick **Windows Boot Manager** in the JARVIS menu (use the arrow keys, then Enter).
  If you don't pick, JARVIS OS starts after a few seconds.
- **From JARVIS OS**: say or type "switch to Windows" to JARVIS, or use the *Restart to Windows* button.
- **If Windows asks for a BitLocker recovery key**: type the 48-digit key from step 2. It only asks once.
- **Last resort**: tap the boot menu key (step 5) and pick **Windows Boot Manager**.

Removing JARVIS OS later is possible too (Windows can delete its partitions and extend C: again), ask first.

---

### USB stick notes (what was tested)

All in virtual machines with a fake Windows disk, not on your real PC:
- The stick written byte for byte (Fedora Media Writer, Rufus **DD mode**): starts on UEFI and old BIOS PCs,
  reaches the safety page and the installer.
- Rufus's default **ISO Image mode** (Rufus copies the files onto a FAT32 stick, renames it CONTAINER-I and fixes
  the boot menu to match): built the way Rufus 4.15 does it, it started on UEFI, reached the safety page (Windows
  found and locked) and the installer, and a full click-through install from that stick worked, with Windows
  byte-identical afterwards (test run 38652813253). Also boots from a GPT-partitioned FAT32 stick.
  This copies what Rufus does; real Rufus on a real stick wasn't run, so DD mode stays the safest pick.
