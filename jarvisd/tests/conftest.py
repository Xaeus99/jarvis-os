import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

class Rec:
    """Records every argv; answers from a table of (argv-prefix -> (code, out))."""
    def __init__(self, answers=None):
        self.calls = []; self.answers = answers or {}
    def __call__(self, argv, timeout=15, input_text=None):
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv), argv   # never a shell string
        self.calls.append(argv)
        for k, v in self.answers.items():
            if tuple(argv[:len(k)]) == k:
                return v(argv) if callable(v) else v
        return 0, ""
    def ran(self, *prefix):
        return any(tuple(c[:len(prefix)]) == prefix for c in self.calls)

EFI = """BootCurrent: 0002
Timeout: 1 seconds
BootOrder: 0002,0000,0001
Boot0000* Windows Boot Manager\tHD(1,GPT,6c2a...,0x800,0x32000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)57494e444f5753000100
Boot0001* UEFI OS\tHD(1,GPT,6c2a...,0x800,0x32000)/File(\\EFI\\BOOT\\BOOTX64.EFI)0000424f
Boot0002* JARVIS OS\tHD(1,GPT,6c2a...,0x800,0x32000)/File(\\EFI\\fedora\\shimx64.efi)
"""

@pytest.fixture
def rec():
    return Rec({("efibootmgr",): (0, EFI), ("wpctl", "get-volume"): (0, "Volume: 0.40\n")})

@pytest.fixture
def brain_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from jarvisd.brain import LinuxBrain
    def make(run, answer=""):
        return LinuxBrain({}, state_dir=str(tmp_path / "state"), run=run, ask=lambda q: answer)
    return make
