"""The JARVIS viewer: a small HUD-styled window that opens when JARVIS finishes making something (shared design with
JARVIS for Windows v5.3).
- an image: shown large, with Copy, Save as, Open folder, Regenerate and Close
- code: syntax-highlighted, with Copy, Save as, Open in editor, Open folder and Close.
  There is deliberately no Run button: JARVIS never runs code it wrote.
Runs as its own process (creator.open_viewer):  python -m jarvisd.viewer image|code PATH [--prompt "..."]
While a game is running the window waits and opens when the game ends, so a game never loses focus."""
from __future__ import annotations
import argparse, json, logging, os, re, shutil, subprocess, sys

from PySide6 import QtCore, QtGui, QtWidgets

log = logging.getLogger("jarvisd.viewer")

def _palette():
    """The colours of the theme he picked for JARVIS (config "theme"; the workspace default is Vibranium)."""
    try:
        from .core import theme as T
        cfg = json.load(open(os.path.expanduser("~/.config/jarvis/config.json")))
        return dict(T.THEMES[T.resolve(cfg.get("theme", "vibranium")) or "vibranium"])
    except Exception:
        pass
    try:
        from .core import theme as T
        return dict(T.THEMES["vibranium"])
    except Exception:
        return dict(main=(92, 225, 255), light=(190, 245, 255), deep=(20, 110, 160), accent=(255, 170, 40), bg0=(4, 10, 20), bg1=(9, 24, 40))
P = _palette()
def C(k, a=255):
    r, g, b = P[k]; return QtGui.QColor(r, g, b, a)
def css(k, a=255):
    r, g, b = P[k]; return f"rgba({r},{g},{b},{a})"

def _font(kind, px, bold=False):
    try:
        from .core import theme
        return theme.font(kind, px, bold)
    except Exception:
        f = QtGui.QFont("Sans Serif"); f.setPixelSize(px); f.setBold(bold); return f

EDITORS = [["code"], ["codium"], ["flatpak", "run", "com.visualstudio.code"], ["kate"], ["kwrite"],
           ["flatpak", "run", "org.kde.kate"], ["flatpak", "run", "org.kde.kwrite"], ["gnome-text-editor"], ["gedit"]]

def editor_argv(path, which=shutil.which, flatpaks=None):
    """A real text editor for the file (never xdg-open: a script's default app could run it)."""
    if flatpaks is None:
        try:
            flatpaks = subprocess.run(["flatpak", "list", "--app", "--columns=application"], capture_output=True, text=True,
                                      timeout=10).stdout.split()
        except Exception:
            flatpaks = []
    for e in EDITORS:
        if e[0] == "flatpak":
            if e[2] in flatpaks and which("flatpak"):
                return e + [path]
        elif which(e[0]):
            return e + [path]
    return None

LANG_OF_EXT = {"py": "python", "js": "js", "ts": "js", "html": "js", "css": "js", "json": "js", "lua": "lua", "sh": "bash",
               "bat": "batch", "ps1": "powershell", "c": "c", "cpp": "cpp", "cs": "csharp", "java": "java", "rs": "rust", "go": "go"}

KEYWORDS = {
    "python": "and as assert async await break class continue def del elif else except False finally for from global if import in is "
              "lambda None nonlocal not or pass raise return True try while with yield self print",
    "js": "async await break case catch class const continue default delete do else export extends false finally for function if "
          "import in instanceof let new null return super switch this throw true try typeof var void while yield console",
    "c": "auto bool break case catch char class const continue default delete do double else enum extern false float for if include "
         "int long namespace new nullptr private protected public return short signed sizeof static struct switch template this "
         "true try typedef unsigned using virtual void while string var foreach in out ref",
    "lua": "and break do else elseif end false for function if in local nil not or repeat return then true until while game workspace",
    "shell": "echo set if else goto call exit rem for in do not exist errorlevel function param foreach return write-host",
}
FAMILY = {"python": "python", "py": "python", "javascript": "js", "js": "js", "typescript": "js", "ts": "js", "html": "js",
          "css": "js", "json": "js", "lua": "lua", "luau": "lua", "batch": "shell", "bat": "shell", "powershell": "shell",
          "ps1": "shell", "bash": "shell", "sh": "shell"}

class Highlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, doc, lang):
        super().__init__(doc)
        fam = FAMILY.get((lang or "").lower(), "c")
        kw = KEYWORDS.get(fam, KEYWORDS["c"]).split()
        def fmt(col, bold=False, italic=False):
            f = QtGui.QTextCharFormat(); f.setForeground(col)
            if bold: f.setFontWeight(QtGui.QFont.Bold)
            if italic: f.setFontItalic(True)
            return f
        self.rules = [
            (QtCore.QRegularExpression(r"\b(?:" + "|".join(map(re.escape, kw)) + r")\b",
                                       QtCore.QRegularExpression.CaseInsensitiveOption if fam == "shell" else QtCore.QRegularExpression.NoPatternOption),
             fmt(C("main"), True)),
            (QtCore.QRegularExpression(r"\b[0-9]+(?:\.[0-9]+)?\b"), fmt(QtGui.QColor(255, 196, 64))),
            (QtCore.QRegularExpression(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\()"), fmt(QtGui.QColor(120, 220, 255))),
            (QtCore.QRegularExpression(r"\"[^\"\n]*\"|'[^'\n]*'|`[^`\n]*`"), fmt(QtGui.QColor(140, 230, 150))),
            (QtCore.QRegularExpression(r"#[^\n]*$" if fam in ("python", "shell") else (r"--[^\n]*$" if fam == "lua" else r"//[^\n]*$")),
             fmt(QtGui.QColor(140, 130, 170), italic=True)),
        ]
        if fam == "shell":
            self.rules.append((QtCore.QRegularExpression(r"(?i)^\s*(?:rem|::)[^\n]*$"), fmt(QtGui.QColor(140, 130, 170), italic=True)))

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


BTN = ("QPushButton {{ color: {light}; background: {bg}; border: 1px solid {main}; border-radius: 6px; padding: 7px 14px; }}"
       "QPushButton:hover {{ background: {hover}; }} QPushButton:pressed {{ background: {main}; color: black; }}")

class ResultViewer(QtWidgets.QWidget):
    """One window per result. kind 'image' -> payload {path, prompt}; kind 'code' -> {path, code, lang, note}."""
    def __init__(self, kind, payload, regenerate=None, parent=None):
        super().__init__(parent, QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        self.kind, self.payload, self.regenerate = kind, dict(payload), regenerate
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle("J.A.R.V.I.S. — " + ("Image" if kind == "image" else "Code"))
        self._drag = None
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(22, 18, 22, 18); lay.setSpacing(12)
        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(("IMAGE  ·  " if kind == "image" else "CODE  ·  ") + self._subtitle())
        title.setFont(_font("display", 15, True)); title.setStyleSheet(f"color: {css('light')}; letter-spacing: 2px;")
        title.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        top.addWidget(title, 1)
        x = QtWidgets.QPushButton("✕"); x.setFixedSize(30, 30); x.clicked.connect(self.close)
        x.setStyleSheet(f"QPushButton {{ color: {css('light')}; background: transparent; border: none; font-size: 16px; }} QPushButton:hover {{ color: {css('accent')}; }}")
        top.addWidget(x)
        lay.addLayout(top)
        if kind == "image":
            self.pix = QtGui.QPixmap(self.payload.get("path", ""))
            self.img = QtWidgets.QLabel(); self.img.setAlignment(QtCore.Qt.AlignCenter); self.img.setMinimumSize(420, 420)
            self.img.setStyleSheet(f"border: 1px solid {css('main', 140)}; border-radius: 8px; background: {css('bg0')};")
            lay.addWidget(self.img, 1)
            self._fit()
        else:
            self.edit = QtWidgets.QPlainTextEdit(); self.edit.setReadOnly(True)
            f = QtGui.QFont("monospace"); f.setStyleHint(QtGui.QFont.Monospace); f.setPixelSize(14); self.edit.setFont(f)
            self.edit.setPlainText(self.payload.get("code", ""))
            self.edit.setStyleSheet(f"QPlainTextEdit {{ color: #EDE6FF; background: {css('bg0')}; border: 1px solid {css('main', 140)};"
                                    f" border-radius: 8px; padding: 8px; selection-background-color: {css('deep')}; }}")
            self.hl = Highlighter(self.edit.document(), self.payload.get("lang", ""))
            lay.addWidget(self.edit, 1)
            note = self.payload.get("note") or ""
            sub = QtWidgets.QLabel(("Not run. " + note).strip()); sub.setWordWrap(True)
            sub.setStyleSheet(f"color: {css('light', 190)};"); sub.setFont(_font("body", 13))
            lay.addWidget(sub)
        row = QtWidgets.QHBoxLayout(); row.setSpacing(8)
        btns = [("Copy", self.copy), ("Save as…", self.save_as)]
        if kind == "image":
            btns += [("Open folder", self.open_folder)]
            if regenerate:
                btns += [("Regenerate", self.regen)]
        else:
            btns += [("Open in editor", self.open_editor), ("Open folder", self.open_folder)]
        btns += [("Close", self.close)]
        self.buttons = {}
        style = BTN.format(light=css("light"), bg=css("bg1", 230), main=css("main"), hover=css("deep", 230))
        for label, fn in btns:
            b = QtWidgets.QPushButton(label); b.setStyleSheet(style); b.setFont(_font("body", 14, True)); b.clicked.connect(fn)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            row.addWidget(b); self.buttons[label] = b
        row.addStretch(1)
        self.status = QtWidgets.QLabel(""); self.status.setStyleSheet(f"color: {css('accent')};"); self.status.setFont(_font("body", 13))
        row.addWidget(self.status)
        lay.addLayout(row)
        self.resize(760, 860 if kind == "image" else 780)

    def _subtitle(self):
        if self.kind == "image":
            p = self.payload.get("prompt", "")
            return (p[:60] + "…") if len(p) > 60 else p
        return os.path.basename(self.payload.get("path", "")) or self.payload.get("lang", "")

    def _fit(self):
        if self.kind == "image" and not self.pix.isNull():
            w = max(200, self.img.width() - 4); h = max(200, self.img.height() - 4)
            self.img.setPixmap(self.pix.scaled(w, h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e); self._fit()

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(2, 2, -2, -2)
        g = QtGui.QLinearGradient(r.topLeft(), r.bottomRight()); g.setColorAt(0, C("bg1", 245)); g.setColorAt(1, C("bg0", 245))
        p.setBrush(g); p.setPen(QtGui.QPen(C("main", 200), 1.5)); p.drawRoundedRect(r, 14, 14)
        # HUD corner brackets
        pen = QtGui.QPen(C("accent"), 3); p.setPen(pen); L = 22
        for (x, y, dx, dy) in ((r.left() + 6, r.top() + 6, 1, 1), (r.right() - 6, r.top() + 6, -1, 1),
                               (r.left() + 6, r.bottom() - 6, 1, -1), (r.right() - 6, r.bottom() - 6, -1, -1)):
            p.drawLine(QtCore.QPointF(x, y), QtCore.QPointF(x + dx * L, y)); p.drawLine(QtCore.QPointF(x, y), QtCore.QPointF(x, y + dy * L))
        p.setPen(QtGui.QPen(C("main", 90), 1)); p.drawLine(QtCore.QPointF(r.left() + 20, r.top() + 54), QtCore.QPointF(r.right() - 20, r.top() + 54))

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & QtCore.Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
    def mouseReleaseEvent(self, e):
        self._drag = None
    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Escape: self.close()
        else: super().keyPressEvent(e)

    def flash(self, text):
        self.status.setText(text); QtCore.QTimer.singleShot(2500, lambda: self.status.setText(""))

    # ---- buttons ----
    def copy(self):
        cb = QtWidgets.QApplication.clipboard()
        if self.kind == "image": cb.setPixmap(self.pix)
        else: cb.setText(self.payload.get("code", ""))
        try:                                           # Wayland clipboard too (wl-clipboard is in the image)
            p = self.payload.get("path", "")
            if self.kind == "image":
                subprocess.Popen(["wl-copy", "--type", "image/png"], stdin=open(p, "rb"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                q = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                q.communicate(self.payload.get("code", "").encode(), timeout=5)
        except Exception as e:
            log.debug("wl-copy: %r", e)
        self.flash("Copied")

    def save_as(self):
        src = self.payload.get("path", "")
        filt = "PNG image (*.png)" if self.kind == "image" else "All files (*)"
        dest, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save as", src, filt)
        if dest:
            try:
                shutil.copyfile(src, dest); self.flash("Saved")
            except Exception as e:
                self.flash("Couldn't save"); log.info("save as: %r", e)

    def open_folder(self):
        p = self.payload.get("path", "")
        argv = ["dolphin", "--select", p] if shutil.which("dolphin") else ["xdg-open", os.path.dirname(p)]
        try:
            subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log.info("open folder: %r", e)

    def open_editor(self):
        argv = editor_argv(self.payload.get("path", ""))
        if not argv:
            self.flash("No text editor found"); return
        try:
            subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log.info("editor: %r", e)

    def regen(self):
        if self.regenerate:
            self.flash("Painting another…")
            self.regenerate(self.payload)


def gaming_now():
    try:
        from . import gamewatch
        return bool((gamewatch.detect() or {}).get("gaming"))
    except Exception:
        return False

def regenerate(payload):
    subprocess.Popen(["jarvisctl", "tell", "make another one"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def payload_for(kind, path, prompt=""):
    if kind == "image":
        return {"path": path, "prompt": prompt}
    code = open(path, encoding="utf-8", errors="replace").read()
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return {"path": path, "code": code, "lang": LANG_OF_EXT.get(ext, ext)}

def main(argv=None):
    ap = argparse.ArgumentParser(prog="jarvis-viewer")
    ap.add_argument("kind", choices=["image", "code"]); ap.add_argument("path"); ap.add_argument("--prompt", default="")
    ap.add_argument("--no-wait", action="store_true", help="open even if a game is running")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    try:
        from .core import theme
        theme.load_fonts()
    except Exception:
        pass
    w = ResultViewer(a.kind, payload_for(a.kind, a.path, a.prompt), regenerate=regenerate if a.kind == "image" else None)
    def show():
        scr = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        w.resize(min(w.width(), scr.width() - 40), min(w.height(), scr.height() - 40))
        w.move(scr.center().x() - w.width() // 2, scr.center().y() - w.height() // 2)
        w.show(); w.raise_(); w.activateWindow()
    if not a.no_wait and gaming_now():
        log.info("a game is running: the viewer opens when it ends")
        t = QtCore.QTimer(); t.setInterval(3000)
        def tick():
            if not gaming_now():
                t.stop(); show()
        t.timeout.connect(tick); t.start()
    else:
        show()
    w.destroyed.connect(app.quit)
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
