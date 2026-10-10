from jarvisd import daemon

def test_open_mic_waits_for_a_microphone_instead_of_crashing():
    tries, sleeps = [], []
    def make(dev):
        tries.append(dev)
        if len(tries) < 3:
            raise RuntimeError("Error querying device -1")
        return "mic"
    assert daemon.open_mic("x", wait_s=30, make=make, sleep=sleeps.append) == "mic"
    assert tries == ["x", "x", "x"] and sleeps == [30, 30]
