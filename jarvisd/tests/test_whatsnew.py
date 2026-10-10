import os
from jarvisd import whatsnew

def test_version_and_once_per_version(tmp_path):
    v = tmp_path / "version"; v.write_text("JARVIS_OS_VERSION=0.4.0-beta\n")
    assert whatsnew.os_version(str(v)) == "0.4.0-beta"
    assert whatsnew.os_version(str(tmp_path / "none")) is None
    seen = str(tmp_path / "state" / "seen")
    assert whatsnew.should_show("0.4.0-beta", seen)
    whatsnew.mark_seen("0.4.0-beta", seen)
    assert not whatsnew.should_show("0.4.0-beta", seen) and whatsnew.should_show("0.5.0", seen)
    assert not whatsnew.should_show(None, seen)

def test_card_renders_with_linux_list(tmp_path):
    from PySide6 import QtCore, QtGui, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    seen = str(tmp_path / "seen")
    card = whatsnew.maybe_show(QtCore, delay_ms=0, version="0.4.0", seen_path=seen)
    assert card is not None and card.lines == whatsnew.LINES and card.title == "JARVIS OS  \u00b7  0.4.0"
    assert whatsnew.maybe_show(QtCore, delay_ms=0, version="0.4.0", seen_path=seen) is None      # once
    img = QtGui.QImage(card.size(), QtGui.QImage.Format_ARGB32); img.fill(0)
    card.render(img)
    out = os.environ.get("JARVIS_SHOT_WN")
    if out: img.save(out)
    assert img.pixelColor(40, 30).alpha() > 0
    card.close()

def test_voice_whats_new(brain_factory, rec):
    b = brain_factory(rec)
    assert b.handle("Jarvis, what's new?") == whatsnew.SPOKEN
    assert any(c[-2:] == ["-m", "jarvisd.whatsnew"] for c in rec.calls)       # the card opens in its own process
    assert b.handle("what's new in jarvis os") == whatsnew.SPOKEN
