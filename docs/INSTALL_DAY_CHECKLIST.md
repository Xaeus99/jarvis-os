# Install day checklist (one page)

For the real PC: Windows 11 + **RTX 5060**, JARVIS OS goes **next to** Windows. Full walk-through:
[INSTALL_DAY_GUIDE.md](INSTALL_DAY_GUIDE.md). If a screen doesn't match, **stop and text Tab a photo**.

**Before (in Windows)**
- [ ] USB stick, **16 GB or more** (it gets wiped). Another phone/device to read this on.
- [ ] Back up Documents, Pictures, Videos, Desktop, game clips, OBS settings, non-cloud saves. Check the copy opens.
- [ ] **BitLocker key**: Start > "Manage BitLocker" (or Settings > Privacy & security > Device encryption). If on:
      save the 48-digit key to your phone (aka.ms/myrecoverykey).
- [ ] Disk Management > right-click **C:** > Shrink Volume > **102400** MB (100 GB; 40960 minimum). Leave the
      **Unallocated** space empty.
- [ ] Release page > download **get-jarvis-os.bat** > double-click. Wait for "checks OK".

**The USB stick (Rufus)**
- [ ] Rufus: Device = your stick, SELECT = the JARVIS `.iso`. **File system: FAT32** (leave it, never NTFS).
- [ ] START > "ISOHybrid image detected" > **Write in DD Image mode** (safest) > OK. 5-15 min.
- [ ] Windows says the stick "needs to be formatted"? Click **Cancel**.

**Boot the stick**
- [ ] Leave **Secure Boot ON**. Stick in a back USB port.
- [ ] Settings > System > Recovery > Advanced startup > **Restart now** > **Use a device** > the stick.
      (No "Use a device"? Tap the boot key at power-on: ASUS F8, MSI F11, Gigabyte F12, ASRock F11.)

**Installer**
- [ ] Purple JARVIS menu: **Install JARVIS OS** > Enter. Black screen up to ~3 min is normal on NVIDIA.
- [ ] "Windows safety check" lists Windows as **LOCKED** > press **Enter**. Type nothing else.
- [ ] Language > Continue. **User Creation**: name, user name, password (remember it), admin ticked > Done.
- [ ] **Installation Destination**: drive ticked, **Automatic** > Done. **Never** "Reclaim space" / "Delete all".
- [ ] **Begin Installation** (10-20 min) > "Complete!" > Reboot System > pull the stick out.

**First restart**
- [ ] Blue **MOK** screen: any key > **Enroll MOK** > **Continue** > **Yes** > type `universalblue` > Enter >
      **Reboot**. (Lets the NVIDIA driver load with Secure Boot on.)
- [ ] JARVIS menu > **JARVIS OS** > arc-reactor splash > log in.
- [ ] Say "Jarvis, gaming check" (RTX 5060 + driver), then "set up my monitor".

**If something's off**
- [ ] Black screen 2+ min: hold power, restart, menu > **last** entry **JARVIS OS - safe graphics**. Text Tab.
- [ ] Back to Windows: menu > **Windows Boot Manager** (or say "switch to Windows").
- [ ] Windows asks for a BitLocker key: type the 48 digits from your phone (only once).
- [ ] Bad update: menu > **previous (rollback)**, or say "roll back the update".
