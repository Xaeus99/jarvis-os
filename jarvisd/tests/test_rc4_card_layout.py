"""rc4: the setup card sizes each row to its text, so a row with a "what to do" line never crowds the next one."""
import pytest

pytest.importorskip("PySide6")

LONG = [("Graphics", "todo", "The NVIDIA driver isn't running the RTX 5060: the Secure Boot key isn't enrolled yet.",
         "Say \u201cfix the graphics driver\u201d, restart, and pick Enroll MOK with the password universalblue."),
        ("Apps", "busy", "Installing your apps from Flathub in the background.", ""),
        ("Offline brain", "todo", "The download stopped at step 1 of 2 because the internet dropped.", "Say \u201cretry the offline brain\u201d when you're back online."),
        ("Microphone", "todo", "No microphone found.", "Plug in your headset or USB mic; I'll notice it on my own within a few seconds."),
        ("Windows", "todo", "I couldn't find the Windows boot entry yet.", "Say \u201cfind Windows\u201d, or restart once and pick Windows from the boot menu."),
        ("Tab link", "todo", "Not linked yet.", "Say \u201clink to Tab\u201d.")]
SHORT = [("Graphics", "ok", "Fine.", "")] * 6

@pytest.fixture(scope="module")
def app():
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

def _check(card):
    rows, end = card.rows()
    assert len(rows) == 6
    for (t, body, y, h), nxt in zip(rows, rows[1:] + [None]):
        bottom = y + card.TITLE_H + h
        nxt_y = nxt[2] if nxt else end
        assert bottom + 8 <= nxt_y, (t, bottom, nxt_y)          # a visible gap before the next title
    assert end + 50 <= card.height()                            # the footer and button sit below the last row
    b = card.btn.geometry()
    assert b.bottom() < card.height() and b.top() >= end        # button inside the card, under the rows
    return rows

def test_wrapped_rows_push_the_next_row_down(app):
    from jarvisd import whatsnew
    rows = _check(whatsnew.make_setup_card("1.0.0-rc4.20261023", its=LONG, seconds=0))
    assert any(h > rows[1][3] for _, _, _, h in rows)           # at least one body really wrapped
    assert rows[1][2] - rows[0][2] > 52                          # and its row grew past the classic step

def test_short_rows_keep_the_classic_layout(app):
    from jarvisd import whatsnew
    card = whatsnew.make_setup_card("1.0.0-rc4.20261023", its=SHORT, seconds=0)
    rows = _check(card)
    assert [y for _, _, y, _ in rows] == [118 + 52 * i for i in range(6)] and card.height() == 505

def test_whats_new_card_still_fits(app):
    from jarvisd import whatsnew
    _check(whatsnew.make_card("1.0.0-rc4.20261023", seconds=0))
