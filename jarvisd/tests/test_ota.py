import json, pytest
from jarvisd.ota import Bootc
from conftest import Rec

STATUS = {"apiVersion": "org.containers.bootc/v1", "kind": "BootcHost", "status": {
  "booted": {"image": {"image": {"image": "ghcr.io/example/jarvis-os:stable", "transport": "registry"},
             "version": "0.1.20261031", "timestamp": "2026-10-31T06:40:00Z", "imageDigest": "sha256:aaa"}},
  "staged": None,
  "rollback": {"image": {"image": {"image": "ghcr.io/example/jarvis-os:stable", "transport": "registry"},
               "version": "0.1.20261030", "timestamp": "2026-10-30T06:40:00Z", "imageDigest": "sha256:bbb"}}}}

def test_status():
    b = Bootc(Rec({("bootc", "status"): (0, json.dumps(STATUS))}))
    st = b.status()
    assert st["booted"]["version"] == "0.1.20261031" and st["rollback"]["digest"] == "sha256:bbb" and st["staged"] is None

def test_check_no_update():
    assert Bootc(Rec({("bootc", "upgrade", "--check"): (0, "No changes in: ghcr.io/example/jarvis-os:stable\n")})).check() is None

def test_switch_channel_refuses_other_registries():
    b = Bootc(Rec())
    with pytest.raises(ValueError):
        b.switch_channel("docker.io/evil/os:latest")
    with pytest.raises(ValueError):
        b.switch_channel("ghcr.io/x/jarvis-os:beta; rm -rf /")
    assert b.switch_channel("ghcr.io/example/jarvis-os:beta")[0]
