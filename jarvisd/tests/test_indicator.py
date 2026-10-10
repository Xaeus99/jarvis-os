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


def test_tray_menu_and_link_dialog(app, tmp_path):
    import json
    from jarvisd.indicator import Tray, tray_actions, code_ok
    assert [l for l, _ in tray_actions(False, None)][-2:] == ["Download offline brain", "Link to Tab\u2026"]
    assert ("Offline brain: ready", None) in tray_actions(True, "ready") and ("Linked to Tab", None) in tray_actions(True, "ready")
    assert code_ok("7kq2-m9xd-r4tb") and not code_ok("hello")
    launched = []
    cfg, st = tmp_path / "config.json", tmp_path / "localai.json"
    t = Tray(str(cfg), str(st), launch=launched.append)
    labels = [a.text() for a in t.menu.actions() if not a.isSeparator()]
    assert "Link to Tab\u2026" in labels and "Download offline brain" in labels
    assert t.link_dialog(ask=lambda: ("7kq2-m9xd-r4tb", True)) is True
    assert launched[-1] == ["jarvisctl", "link", "--code", "7KQ2-M9XD-R4TB"]
    cfg.write_text(json.dumps({"forwarding": {"to": "x@example.invalid"}})); st.write_text(json.dumps({"status": "ready"}))
    t.rebuild()
    labels = [a.text() for a in t.menu.actions() if not a.isSeparator()]
    assert "Linked to Tab" in labels and "Offline brain: ready" in labels
