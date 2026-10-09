import pytest
from jarvisd import switchos
from conftest import EFI

def test_parse_efibootmgr():
    p = switchos.parse_efibootmgr(EFI)
    assert p["current"] == "0002" and p["order"] == ["0002", "0000", "0001"]
    assert p["entries"]["0000"] == "Windows Boot Manager" and p["entries"]["0002"] == "JARVIS OS"

def test_windows_bootnext_only_sets_next_boot():
    steps = switchos.windows_bootnext_argv(EFI)
    assert steps == [["efibootmgr", "--bootnext", "0000"], ["systemctl", "reboot"]]
    assert not any("--bootorder" in s or "-o" in s for s in steps[0])     # the permanent order is never changed

def test_no_windows_entry_raises():
    with pytest.raises(LookupError):
        switchos.windows_bootnext_argv("BootCurrent: 0001\nBoot0001* JARVIS OS\tHD(...)\n")

BCD = """Firmware Boot Manager
---------------------
identifier              {fwbootmgr}
displayorder            {bootmgr}
                        {7d1a2c40-1111-2222-3333-444455556666}
timeout                 1

Windows Boot Manager
--------------------
identifier              {bootmgr}
device                  partition=\\Device\\HarddiskVolume1
description             Windows Boot Manager

Firmware Application (101fffff)
-------------------------------
identifier              {7d1a2c40-1111-2222-3333-444455556666}
device                  partition=\\Device\\HarddiskVolume1
path                    \\EFI\\fedora\\shimx64.efi
description             JARVIS OS
"""

def test_bcdedit_bootsequence():
    steps = switchos.jarvis_bootsequence_argv(BCD)
    assert steps[0] == ["bcdedit", "/set", "{fwbootmgr}", "bootsequence", "{7d1a2c40-1111-2222-3333-444455556666}"]
    assert steps[1][0] == "shutdown"

def test_bcdedit_fedora_label_also_found():
    assert switchos.jarvis_bootsequence_argv(BCD.replace("JARVIS OS", "Fedora"))[0][-1].startswith("{7d1a")

def test_bcdedit_no_entry():
    with pytest.raises(LookupError):
        switchos.jarvis_bootsequence_argv(BCD.replace("JARVIS OS", "Ubuntu"))
