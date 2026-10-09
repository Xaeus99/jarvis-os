# JARVIS OS

J.A.R.V.I.S. as an operating system: Bazzite (KDE Plasma, Steam/Proton, NVIDIA open drivers) plus the JARVIS
layer, shipped as a signed bootc image so Tab can update it over the air. Dual-boots next to Windows and never
modifies Windows.

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

    cd jarvisd && QT_QPA_PLATFORM=offscreen python -m pytest -q tests      # 77 tests

## Status (phase 1, Oct 9 2026)

Built and tested on a Linux build box: boot theme (real UEFI boot in QEMU), jarvisd brain + Linux hands
(fake command runner), remote-control policy + kill switch, Qt indicator (offscreen and on a real Plasma 5
session under Xvfb), wake word with the real openWakeWord model on synthetic speech.
NOT yet done: the image has never been built (needs ~15 GB + podman; done in CI), nothing has run on real
hardware, the Tab channel/HUD/workspace are not ported yet.

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
