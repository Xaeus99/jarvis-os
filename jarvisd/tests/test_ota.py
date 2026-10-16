import json, pytest
from jarvisd.ota import Bootc
from conftest import Rec
H = ("pkexec", "/usr/libexec/jarvis-os/os-update")

STATUS = {"apiVersion": "org.containers.bootc/v1", "kind": "BootcHost", "status": {
  "booted": {"image": {"image": {"image": "ghcr.io/example/jarvis-os:stable", "transport": "registry"},
             "version": "0.1.20261031", "timestamp": "2026-10-31T06:40:00Z", "imageDigest": "sha256:aaa"}},
  "staged": None,
  "rollback": {"image": {"image": {"image": "ghcr.io/example/jarvis-os:stable", "transport": "registry"},
               "version": "0.1.20261030", "timestamp": "2026-10-30T06:40:00Z", "imageDigest": "sha256:bbb"}}}}

def test_status():
    b = Bootc(Rec({H + ("status",): (0, json.dumps(STATUS))}))
    st = b.status()
    assert st["booted"]["version"] == "0.1.20261031" and st["rollback"]["digest"] == "sha256:bbb" and st["staged"] is None

def test_check_no_update():
    assert Bootc(Rec({H + ("check",): (0, "No changes in: ghcr.io/example/jarvis-os:stable\n")})).check() is None

def test_switch_channel_refuses_other_registries():
    b = Bootc(Rec())
    with pytest.raises(ValueError):
        b.switch_channel("docker.io/evil/os:latest")
    with pytest.raises(ValueError):
        b.switch_channel("ghcr.io/x/jarvis-os:beta; rm -rf /")
    with pytest.raises(ValueError):
        b.switch_channel("ghcr.io/example/jarvis-os:sha256-deadbeef")
    assert b.switch_channel("ghcr.io/example/jarvis-os:beta")[0]
    assert b.run.calls[-1] == list(H) + ["switch", "beta"]

def test_every_call_goes_through_root_helper():
    r = Rec({H + ("status",): (0, json.dumps(STATUS)), H + ("check",): (0, "No changes in: x\n")})
    b = Bootc(r); b.status(); b.check(); b.stage(); b.rollback()
    assert [c[2] for c in r.calls] == ["status", "check", "stage", "rollback"]
    assert all(c[:2] == list(H) for c in r.calls)

def test_helper_script_and_polkit_files():
    import pathlib, re, subprocess
    root = pathlib.Path(__file__).resolve().parents[2] / "image" / "system_files"
    h = root / "usr/libexec/jarvis-os/os-update"
    assert subprocess.run(["bash", "-n", str(h)]).returncode == 0
    t = h.read_text()
    for verb in ("status)", "check)", "stage)", "rollback)", "switch)"):
        assert verb in t
    assert "reboot" not in re.sub(r"#.*", "", t)               # never reboots by itself
    pol = (root / "usr/share/polkit-1/actions/os.jarvis.os-update.policy").read_text()
    assert "/usr/libexec/jarvis-os/os-update" in pol and "<allow_any>no</allow_any>" in pol
    rule = (root / "usr/share/polkit-1/rules.d/49-jarvis-os-update.rules").read_text()
    assert 'action.id == "os.jarvis.os-update"' in rule and 'isInGroup("wheel")' in rule
