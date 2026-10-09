# JARVIS OS

J.A.R.V.I.S. as an operating system: Bazzite (KDE Plasma, Steam/Proton, NVIDIA open drivers) plus the JARVIS
layer, shipped as a signed bootc image so Tab can update it over the air. Dual-boots next to Windows and never
modifies Windows.

**📖 [How to get JARVIS: the tutorial](docs/TUTORIAL.md)** (Windows PC, build your own, dual-boot install)

<img src="docs/img/jarvis_os_desktop_clean.jpg" width="100%" alt="JARVIS OS desktop: KDE Plasma 6 with the Vibranium theme">

## Get your own Tab

JARVIS sends anything that isn't a built-in command to **Tab**, your own AI assistant that texts, browses and gets things done.
Get one with this invite: **[https://tab.bot/r/856A517B3CCACA47](https://tab.bot/r/856A517B3CCACA47)**

| Path | What |
|---|---|
| `image/Containerfile` | the OS image: `FROM ghcr.io/ublue-os/bazzite-nvidia-open:stable@sha256:…` + `build_files/build.sh` |
| `image/system_files/` | everything copied into the image: GRUB theme, Plasma colour scheme, wallpapers, fonts, systemd units, polkit, helpers |
| `image/disk_config/iso.toml` | installer ISO config for bootc-image-builder (never auto-partitions) |
| `jarvisd/` | the Linux JARVIS daemon (Python). `jarvisd/core/` is vendored verbatim from Windows JARVIS v5.1 |
| `windows/` | Windows-side "switch to JARVIS OS" (one-time firmware boot via bcdedit) |
| `bootdemo/` | builds a GRUB ISO with the real theme and boots it in QEMU/OVMF for a screenshot |
| `.github/workflows/` | nightly image build + test + push + cosign sign; manual ISO build |
| `cosign.pub` | public half of the image-signing key (the private half lives only in the repo's Actions secrets) |
| `tools/make_art.py` | regenerates wallpapers and boot art |

## Test

    cd jarvisd && QT_QPA_PLATFORM=offscreen python -m pytest -q tests      # 85 tests

## Status (phase 2, Oct 9 2026)

Phase 2 adds the public repo, signed cloud builds, the first-login app installer and "restore my games".
The image itself has not booted on real hardware yet.

### Phase 1

Built and tested on a Linux build box: boot theme (real UEFI boot in QEMU), jarvisd brain + Linux hands
(fake command runner), remote-control policy + kill switch, Qt indicator (offscreen and on a real Plasma 5
session under Xvfb), wake word with the real openWakeWord model on synthetic speech.
NOT yet done: the image has never been built (needs ~15 GB + podman; done in CI), nothing has run on real
hardware, the Tab channel/HUD/workspace are not ported yet.

## Apps and games

- First login: `jarvis-first-boot-apps.service` installs `image/system_files/usr/share/jarvis-os/apps/flatpaks.list` from Flathub (per user, retried at the next login if anything fails).
- "Jarvis, restore my games" (or the *Restore my games* menu entry, or `jarvisctl games restore`) mounts the Windows drive **read-only**, reads its Steam libraries, skips games that only run on Windows (`steam-compat.json`), and queues the rest so Steam downloads them on its next start. Launch-option tweaks (e.g. Marvel Rivals' `SteamDeck=1 %command%`) are printed. No game list ships in the image; it is read from your own library.

## Updates and signing

Every push to `main` (and a nightly run) builds `ghcr.io/<owner>/jarvis-os:beta`, pushes it, and signs it with cosign.
The image ships `cosign.pub` and a `policy.json` entry, so `bootc upgrade` only accepts images signed with this key.

## Licences

JARVIS OS's own code: MIT. Fonts: SIL OFL (see `image/system_files/usr/share/fonts/jarvis`). Wake-word models: see
`image/system_files/usr/lib/jarvis-os/models/openwakeword/NOTICE.md`. Base image: Bazzite (Universal Blue).

## Safety promises

- The installer never auto-partitions and nothing here ever writes to the Windows partition.
- Tab remote control is off unless the owner turns it on, always shows a "TAB IS DRIVING" bar, and
  Meta+Shift+Esc or "take back control" ends it instantly. Purchases, messages and destructive actions always ask first.
