from jarvisd import daemon

def test_open_mic_waits_for_a_microphone_instead_of_crashing():
    tries, sleeps = [], []
    def make(dev):
        tries.append(dev)
        if len(tries) < 3:
            raise RuntimeError("Error querying device -1")
        return "mic"
    assert daemon.open_mic("x", wait_s=30, make=make, sleep=sleeps.append, probe=lambda: None) == "mic"
    assert tries == ["x", "x", "x"] and sleeps == [30, 30]


def test_no_mic_retries_ask_pipewire_not_the_audio_driver(caplog):
    """Each failed PortAudio open logs an ALSA error line; while PipeWire lists no mic, retries don't open it."""
    import logging
    caplog.set_level(logging.INFO, logger="jarvisd")
    tries, sleeps, answers = [], [], iter([False, False, False, True])
    def make(dev):
        tries.append(dev)
        if len(tries) == 1:
            raise RuntimeError("Unanticipated host error [ALSA error -2]")
        return "mic"
    assert daemon.open_mic(None, make=make, sleep=sleeps.append, probe=lambda: next(answers)) == "mic"
    assert len(tries) == 2 and len(sleeps) == 4          # 1 real try, 3 quiet PipeWire checks, then the mic opens
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("no microphone yet" in m for m in msgs) == 1 and any("microphone found" in m for m in msgs)

def test_has_input_source_reads_pactl():
    import subprocess
    def run_with(out, rc=0):
        return lambda argv, **kw: subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")
    mon = "52\talsa_output.pci-0000_00_1f.3.analog-stereo.monitor\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED\n"
    mic = "53\talsa_input.usb-Mic-00.mono-fallback\tPipeWire\ts16le 1ch 48000Hz\tSUSPENDED\n"
    assert daemon.has_input_source(run_with(mon)) is False
    assert daemon.has_input_source(run_with("")) is False
    assert daemon.has_input_source(run_with(mon + mic)) is True
    assert daemon.has_input_source(run_with("", rc=1)) is None
    def missing(argv, **kw): raise FileNotFoundError("pactl")
    assert daemon.has_input_source(missing) is None
