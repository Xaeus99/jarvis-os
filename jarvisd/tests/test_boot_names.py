"""Boot names: firmware entry "JARVIS OS" (efi-label) and boot-menu titles (boot-titles)."""
import importlib.machinery, importlib.util, os, subprocess

LIBEXEC = os.path.join(os.path.dirname(__file__), "..", "..", "image", "system_files", "usr", "libexec", "jarvis-os")
UUID = "7a6f575c-8b79-410a-bf89-dc595142cd8f"
BASE = ("BootCurrent: 0004\nBootOrder: 0004,0002,0000,0003\n"
        "Boot0000* UiApp\tFvVol(7cb8bdc9-f8eb-4f34-aaea-3ee4af6516a1)/FvFile(462caa21-7614-4503-836e-8ab6f4662331)\n"
        "Boot0002* UEFI Misc Device\tPciRoot(0x0)/Pci(0x2,0x0){auto_created_boot_option}\n"
        "Boot0004* Bazzite\tHD(1,GPT," + UUID + ",0x800,0x32000)/\\EFI\\fedora\\shimx64.efi\n")
WIN = "Boot0001* Windows Boot Manager\tHD(1,GPT," + UUID + ",0x800,0x32000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)\n"

def _efi():
    path = os.path.join(LIBEXEC, "efi-label")
    loader = importlib.machinery.SourceFileLoader("efi_label", path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("efi_label", loader))
    loader.exec_module(mod)
    return mod

def test_renames_the_base_entry_only():
    what, old = _efi().plan(BASE + WIN)
    assert what == "rename" and old["num"] == "0004" and old["label"] == "Bazzite" and old["part"] == 1

def test_windows_entry_is_never_ours():
    e = _efi()
    _, entries = e.parse(WIN)
    assert entries and not any(e.ours(x) for x in entries)
    assert e.plan(WIN)[0] == "none"

def test_already_named_is_a_no_op_and_duplicates_go():
    e = _efi()
    named = BASE.replace("Bazzite", "JARVIS OS")
    assert e.plan(named) == ("ok", None)
    dup = named + "Boot0007* Bazzite\tHD(1,GPT," + UUID + ",0x800,0x32000)/\\EFI\\fedora\\shimx64.efi\n"
    assert e.plan(dup) == ("dedupe", ["0007"])

def test_boot_titles(tmp_path):
    (tmp_path / "ostree-2-default.conf").write_text("title JARVIS OS 0.1.1 (Vibranium) (ostree:0)\nversion 2\n")
    (tmp_path / "ostree-1-default.conf").write_text("title JARVIS OS 0.1.1 (Vibranium) (ostree:1)\nversion 1\n")
    env = dict(os.environ, JARVIS_BLS_DIR=str(tmp_path))
    for _ in range(2):   # idempotent
        assert subprocess.run(["bash", os.path.join(LIBEXEC, "boot-titles")], env=env).returncode == 0
    assert (tmp_path / "ostree-2-default.conf").read_text().splitlines()[0] == "title JARVIS OS 0.1.1 (Vibranium)"
    assert (tmp_path / "ostree-1-default.conf").read_text().splitlines()[0] == "title JARVIS OS 0.1.1 (Vibranium) - previous (rollback)"
