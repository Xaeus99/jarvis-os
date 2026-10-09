"""The real Qt indicator, offscreen: appears when Tab drives, its button is the kill switch."""
import os
import pytest
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore
from jarvisd.remote_control import RemoteControl
from jarvisd.indicator import Indicator

@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

def test_indicator_shows_and_kill_button_works(app, tmp_path):
    rc = RemoteControl(str(tmp_path / "remote.json"))
    ind = Indicator(rc)
    assert ind.pills == []
    rc.grant(5); ind.sync()
    assert len(ind.pills) >= 1 and ind.pills[0].isVisible()
    labels = [w.text() for w in ind.pills[0].findChildren(QtWidgets.QLabel)]
    assert "TAB IS DRIVING" in labels
    btn = ind.pills[0].findChild(QtWidgets.QPushButton)
    assert btn.text() == "TAKE BACK CONTROL"
    btn.click()
    assert not rc.driving
    ind.sync(); assert ind.pills == []
