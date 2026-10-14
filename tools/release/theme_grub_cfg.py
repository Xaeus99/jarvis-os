#!/usr/bin/env python3
"""JARVIS look for the install media's boot menu (GRUB, UEFI and BIOS). Used by tools/release/brand_iso.sh.
  theme_grub_cfg.py IN_GRUB_CFG OUT_GRUB_CFG
Keeps every menu entry, its order and its kernel/initrd lines byte-for-byte (the tests press the same keys and the
installer boots the same way); changes only the titles ("Install JARVIS OS", not "... 44") and adds the theme block
right after the line that finds the install media, so the theme is read from the ISO9660 filesystem in all three
places the menu lives (EFI/BOOT/grub.cfg, the same file inside images/efiboot.img, boot/grub2/grub.cfg).
If the theme can't load (old firmware, no graphics), GRUB keeps its plain text menu in JARVIS colours."""
import re, sys

THEME_DIR = "/boot/grub2/themes/jarvis-installer"
BLOCK = f"""
# >>> JARVIS OS installer boot menu look (tools/release/brand_iso.sh)
set menu_color_normal=light-gray/black
set menu_color_highlight=white/magenta
set color_normal=light-gray/black
set timeout_style=menu
insmod gfxterm
insmod gfxmenu
insmod png
set jt=($root){THEME_DIR}
loadfont $jt/orbitron_36.pf2
loadfont $jt/rajdhani_20.pf2
loadfont $jt/rajdhani_24.pf2
loadfont $jt/rajdhani_28.pf2
if terminal_output gfxterm; then set theme=$jt/theme.txt; export theme; fi
# <<< JARVIS OS installer boot menu look
"""
TITLES = [  # (pattern on the title, new title)
    (r"^Test this media & install JARVIS OS( \d+)?$", "Check this USB stick, then install JARVIS OS"),
    (r"^Install JARVIS OS( \d+)? in basic graphics mode$", "Install JARVIS OS in basic graphics mode"),
    (r"^Install JARVIS OS( \d+)?$", "Install JARVIS OS"),
]

BIOS_VIDEO = """insmod vbe
insmod vga
insmod font
set gfxmode=1024x768x32,1024x768,auto
"""

def theme(cfg: str, bios: bool = False) -> str:
    if "JARVIS OS installer boot menu look" in cfg:
        raise SystemExit("already themed")
    m = re.search(r"^search [^\n]*--set=root[^\n]*\n", cfg, re.M)
    if not m:
        raise SystemExit("no 'search --set=root' line: unexpected grub.cfg")
    block = BLOCK.replace("insmod gfxterm\n", BIOS_VIDEO + "insmod gfxterm\n") if bios else BLOCK
    out = cfg[:m.end()] + block + cfg[m.end():]
    def retitle(mm):
        t = mm.group(2)
        for pat, new in TITLES:
            if re.match(pat, t):
                return f"{mm.group(1)}'{new}'"
        return mm.group(0)
    out = re.sub(r"^(\s*(?:menuentry|submenu) )'([^']*)'", retitle, out, flags=re.M)
    # safety: the boot lines must be unchanged and in the same order
    pick = lambda s: re.findall(r"^\s*(?:linux|initrd|chainloader)\b.*$", s, re.M)
    assert pick(out) == pick(cfg), "kernel/initrd lines changed"
    assert len(re.findall(r"^\s*menuentry ", out, re.M)) == len(re.findall(r"^\s*menuentry ", cfg, re.M))
    return out

if __name__ == "__main__":
    # --bios: the BIOS menu (boot/grub2/grub.cfg) also loads the VESA video drivers and asks for 1024x768: run
    # 38483213800 showed the BIOS menu in text mode (JARVIS colours, new titles) while UEFI drew the full theme
    src = open(sys.argv[1]).read()
    open(sys.argv[2], "w").write(theme(src, bios="--bios" in sys.argv[3:]))
    print("themed", sys.argv[1], "->", sys.argv[2])
