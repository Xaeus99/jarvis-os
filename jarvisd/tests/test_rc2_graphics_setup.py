import json, os, types
import pytest
from jarvisd import gpucheck, setupcheck, vrpair, brain, whatsnew

def fake_sys(tmp, gpus=(), module=False, version=None, modeset=None, sb=None):
    s = tmp / "sys"
    for slot, vendor, dev, drv in gpus:
        d = s / "bus/pci/devices" / slot
        d.mkdir(parents=True)
        (d / "class").write_text("0x030000\n"); (d / "vendor").write_text(vendor + "\n"); (d / "device").write_text(dev + "\n")
        if drv:
            dd = s / "bus/pci/drivers" / drv; dd.mkdir(parents=True, exist_ok=True)
            os.symlink(dd, d / "driver")
    # a non-display device that must be ignored
    n = s / "bus/pci/devices/0000:00:1f.3"; n.mkdir(parents=True, exist_ok=True)
    (n / "class").write_text("0x040300\n"); (n / "vendor").write_text("0x8086\n"); (n / "device").write_text("0x7f50\n")
    if module:
        (s / "module/nvidia").mkdir(parents=True)
        if version: (s / "module/nvidia/version").write_text(version + "\n")
    if modeset is not None:
        (s / "module/nvidia_drm/parameters").mkdir(parents=True); (s / "module/nvidia_drm/parameters/modeset").write_text(modeset + "\n")
    if sb is not None:
        (s / "firmware/efi/efivars").mkdir(parents=True)
        (s / "firmware/efi/efivars" / gpucheck.SB_VAR).write_bytes(b"\x06\x00\x00\x00" + (b"\x01" if sb else b"\x00"))
    return str(s)

def fake_proc(tmp, cmdline="BOOT_IMAGE=/vmlinuz root=UUID=x rw nvidia-drm.modeset=1"):
    p = tmp / "proc"; p.mkdir(exist_ok=True); (p / "cmdline").write_text(cmdline + "\n"); return str(p)

class Run:
    def __init__(self, out=None): self.out = out or {}; self.calls = []
    def __call__(self, argv, capture_output=True, text=True, timeout=10, **kw):
        self.calls.append(argv)
        rc, o = self.out.get(argv[0], (1, ""))
        return types.SimpleNamespace(returncode=rc, stdout=o, stderr="")

RTX5060 = ("0000:01:00.0", "0x10de", "0x2d05", "nvidia")

def test_rtx50_ids():
    assert gpucheck.is_rtx50(0x2D05) and gpucheck.is_rtx50(0x2B85) and not gpucheck.is_rtx50(0x2684)   # 4090 is Ada

def test_ok_rtx5060(tmp_path):
    run = Run({"nvidia-smi": (0, "NVIDIA GeForce RTX 5060, 580.95.05, 41\n")})
    v = gpucheck.check(save=False, sysfs=fake_sys(tmp_path, [RTX5060], True, "580.95.05", "Y", True), proc=fake_proc(tmp_path), run=run)
    assert v["code"] == "ok" and v["ok"]
    assert "RTX 5060" in v["headline"] and "580.95.05" in v["headline"] and "Secure Boot is on" in v["headline"]
    assert not any(c[0] in ("journalctl", "mokutil") for c in run.calls)       # nothing else read when all is well
    assert v["short"] == "GeForce RTX 5060 on the NVIDIA driver 580.95.05, Secure Boot on."     # the setup card's one line
    assert setupcheck.graphics_item(v)[2] == v["short"]

def test_no_nvidia_in_a_vm(tmp_path):
    v = gpucheck.check(save=False, sysfs=fake_sys(tmp_path, [("0000:00:01.0", "0x1234", "0x1111", "bochs-drm")]), proc=fake_proc(tmp_path), run=Run())
    assert v["code"] == "no_nvidia" and v["ok"] and v["fix"] == ""

def test_secure_boot_blocked(tmp_path):
    run = Run({"journalctl": (0, "Loading of module with unavailable key is rejected\n"), "mokutil": (0, "")})
    v = gpucheck.check(save=False, sysfs=fake_sys(tmp_path, [RTX5060[:3] + (None,)], sb=True), proc=fake_proc(tmp_path), run=run)
    assert v["code"] == "secure_boot_key" and not v["ok"]
    assert "GeForce RTX 5060" in v["headline"] and "universalblue" in v["fix"] and "enroll-secure-boot-key" in v["fix"]

def test_secure_boot_key_already_queued(tmp_path):
    run = Run({"journalctl": (0, ""), "mokutil": (0, "[key 1]\nSubject: CN=ublue kernel\n")})
    v = gpucheck.check(save=False, sysfs=fake_sys(tmp_path, [RTX5060[:3] + (None,)], sb=True), proc=fake_proc(tmp_path), run=run)
    assert v["code"] == "secure_boot_key" and "already queued" in v["fix"] and v["facts"]["mok_pending"]

def test_safe_graphics_entry(tmp_path):
    v = gpucheck.check(save=False, sysfs=fake_sys(tmp_path, [RTX5060[:3] + (None,)], sb=True),
                       proc=fake_proc(tmp_path, "BOOT_IMAGE=/vmlinuz rw nomodeset modprobe.blacklist=nvidia,nvidia_drm"), run=Run())
    assert v["code"] == "safe_graphics" and v["ok"] and "JARVIS OS" in v["fix"]

def test_unsupported_and_failed_and_nouveau_and_modeset(tmp_path):
    a = tmp_path / "a"; a.mkdir()
    v = gpucheck.check(save=False, sysfs=fake_sys(a, [RTX5060[:3] + (None,)], module=True, sb=False), proc=fake_proc(a),
                       run=Run({"journalctl": (0, "NVRM: The NVIDIA GPU 0000:01:00.0 (PCI ID: 10de:2d05) is not supported by the 550.1 driver\n")}))
    assert v["code"] == "unsupported"
    b = tmp_path / "b"; b.mkdir()
    v = gpucheck.check(save=False, sysfs=fake_sys(b, [RTX5060[:3] + (None,)], module=True, sb=False), proc=fake_proc(b),
                       run=Run({"journalctl": (0, "NVRM: RmInitAdapter failed! (0x22:0x38:860)\n")}))
    assert v["code"] == "driver_failed" and "roll back" in v["fix"]
    c = tmp_path / "c"; c.mkdir()
    v = gpucheck.check(save=False, sysfs=fake_sys(c, [RTX5060[:3] + ("nouveau",)]), proc=fake_proc(c), run=Run())
    assert v["code"] == "nouveau"
    d = tmp_path / "d"; d.mkdir()
    v = gpucheck.check(save=False, sysfs=fake_sys(d, [RTX5060], True, "580", "N"), proc=fake_proc(d), run=Run())
    assert v["code"] == "modeset_off" and not v["ok"]

def test_saves_state_and_notifies_only_on_problems(tmp_path, monkeypatch):
    monkeypatch.setattr(gpucheck, "STATE", str(tmp_path / "st" / "gpu-check.json"))
    v = gpucheck.check(sysfs=fake_sys(tmp_path, [RTX5060], True, "580", "Y"), proc=fake_proc(tmp_path), run=Run())
    assert json.load(open(gpucheck.STATE))["code"] == "ok"
    run = Run(); assert gpucheck.notify(v, run=run) is False and not run.calls
    assert gpucheck.notify({"ok": False, "headline": "x", "fix": "y"}, run=run) is True and run.calls[0][0] == "notify-send"

def test_runner_adapter():
    r = gpucheck.runner_adapter(lambda argv, timeout=15: (0, "hi"))
    p = r(["echo"], capture_output=True, text=True, timeout=3)
    assert p.returncode == 0 and p.stdout == "hi"

# ---- setup check ----
def test_setup_items_first_boot(tmp_path):
    st = tmp_path / "state"; st.mkdir()
    (st / "localai.json").write_text(json.dumps({"status": "installing", "step": 1, "progress": 37}))
    run = Run({"systemctl": (0, "active\n"), "pactl": (0, "50\talsa_output.pci.monitor\tPipeWire\n51\talsa_input.usb-mic\tPipeWire\n"),
               "efibootmgr": (0, "Boot0000* Windows Boot Manager\tHD(1,GPT)\nBoot0001* JARVIS OS\n")})
    gpu = {"code": "ok", "ok": True, "headline": "The GeForce RTX 5060 is running on the NVIDIA driver.", "fix": ""}
    its = setupcheck.items(run=run, gpu=gpu, state_dir=str(st), config=str(tmp_path / "none.json"))
    states = {i[0]: i[1] for i in its}
    assert states == {"Graphics": "ok", "Apps": "busy", "Offline brain": "busy", "Microphone": "ok", "Windows": "ok", "Tab link": "todo"}
    s = setupcheck.summary(its)
    assert "still going on its own: apps, offline brain" in s.lower() and "needs you: tab link" in s.lower()
    assert "link to Tab" in setupcheck.spoken(its)
    lines = setupcheck.card_lines(its)
    assert len(lines) == 6 and lines[0][0].startswith("\u2713") and "37%" in lines[2][1]

def test_setup_everything_done(tmp_path):
    st = tmp_path / "state"; st.mkdir(); (st / "apps.done").write_text("ok\n")
    (st / "localai.json").write_text(json.dumps({"status": "ready"}))
    cfg = tmp_path / "config.json"; cfg.write_text(json.dumps({"forwarding": {"to": "x"}, "replies": {"key": "k"}}))
    run = Run({"pactl": (0, "51\talsa_input.usb\tPipeWire\n"), "efibootmgr": (0, "Boot0000* Windows Boot Manager\n")})
    its = setupcheck.items(run=run, gpu={"code": "no_nvidia", "ok": True, "headline": "", "fix": ""}, state_dir=str(st), config=str(cfg))
    assert setupcheck.summary(its) == "Everything's set up."

def test_setup_problems(tmp_path):
    st = tmp_path / "state"; st.mkdir()
    run = Run({"systemctl": (3, "failed\n"), "pactl": (0, "50\talsa_output.pci.monitor\tPipeWire\n"), "efibootmgr": (0, "Boot0001* JARVIS OS\n")})
    gpu = {"code": "secure_boot_key", "ok": False, "headline": "Secure Boot stopped the NVIDIA driver.", "fix": "Say fix the graphics driver."}
    its = setupcheck.items(run=run, gpu=gpu, state_dir=str(st), config=str(tmp_path / "x.json"))
    states = {i[0]: i[1] for i in its}
    assert states["Graphics"] == "todo" and states["Apps"] == "warn" and states["Microphone"] == "todo" and states["Windows"] == "warn"
    assert "fix the graphics driver" in setupcheck.text(its)

# ---- VR pairing stub ----
def test_vr_pairing_code(tmp_path):
    p = str(tmp_path / "vr.json")
    rec = vrpair.begin_pairing(now=1000, path=p, ip="192.168.1.20", rng=lambda n: 42)
    assert rec["code"] == "000042" and rec["expires"] == 1000 + vrpair.CODE_TTL_S
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"
    assert not vrpair.check_code("000041", now=1001, path=p)
    assert not vrpair.check_code("000042", now=1000 + vrpair.CODE_TTL_S + 1, path=p)
    assert vrpair.check_code("000042", now=1001, path=p) and not os.path.exists(p)      # one use
    assert "isn't on JARVIS OS yet" in vrpair.pair_line()

# ---- voice ----
@pytest.mark.parametrize("said,action", [
    ("check my graphics card", "gpu_check"), ("jarvis, is my gpu working", "gpu_check"), ("graphics check", "gpu_check"),
    ("check the nvidia driver", "gpu_check"), ("fix the graphics driver", "gpu_fix"), ("enroll the secure boot key", "gpu_fix"),
    ("how's setup going", "setup_status"), ("is everything set up?", "setup_status"), ("setup status", "setup_status"),
    ("pair my headset", "pair_headset"), ("pair my quest", "pair_headset"), ("connect my vr headset", "pair_headset"),
    ("what's my gpu temperature", "gpu_stats"), ("run a gaming check", "gaming_check"),
])
def test_voice_phrases(said, action):
    assert brain.parse_os(said)[0] == action

# ---- first-login setup card ----
def test_first_login_shows_setup_card(tmp_path, monkeypatch):
    shown = {}
    class Card:
        def __init__(self): self.lines = None; self.title = ""; self.subtitle = ""
        def show(self): pass
    monkeypatch.setattr(whatsnew, "make_card", lambda v=None, seconds=60: Card())
    monkeypatch.setattr(setupcheck, "items", lambda: [("Graphics", "ok", "fine", "")] * 6)
    class QC:
        class QTimer:
            @staticmethod
            def singleShot(ms, fn): shown["ms"] = ms
    seen = str(tmp_path / "seen")
    c = whatsnew.maybe_show(QC, version="1.0.0-rc2.20261022", seen_path=seen)
    assert c.title.startswith("JARVIS OS setup") and c.kicker == "FIRST START" and len(c.lines) == 6
    whatsnew.mark_seen("1.0.0-rc1.20261016", seen)                     # later: an update shows what's new instead
    c = whatsnew.maybe_show(QC, version="1.0.0-rc2.20261022", seen_path=seen)
    assert not c.title.startswith("JARVIS OS setup")

def test_whatsnew_fits_the_card():
    assert len(whatsnew.LINES) <= 6
