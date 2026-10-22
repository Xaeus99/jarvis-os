"""Webcam features were removed from JARVIS OS in 1.0.0-rc2 (welcome-back greeting, learn/forget my face, camera
greeting on/off). The old phrases answer "That's been removed, sir." and saved face data is deleted."""
import os, pathlib, re
from jarvisd import camera

PKG = pathlib.Path(__file__).resolve().parents[1] / "jarvisd"

def test_old_phrases_answer_removed_and_delete_face_data(brain_factory, rec):
    b = brain_factory(rec)
    faces = os.path.join(b.state_dir, "faces"); os.makedirs(faces, exist_ok=True)
    open(os.path.join(faces, "owner_lbph.yml"), "w").write("x")
    for said in ["learn my face", "forget my face", "turn on the camera", "turn off the face greeting", "enable the webcam"]:
        assert b.handle(said) == "That's been removed, sir.", said
    assert not os.path.exists(faces)

def test_purge_face_data(tmp_path):
    assert camera.purge_face_data(str(tmp_path)) is False
    (tmp_path / "faces").mkdir(); (tmp_path / "faces" / "owner_00.png").write_bytes(b"x")
    assert camera.purge_face_data(str(tmp_path)) is True and not (tmp_path / "faces").exists()

def test_daemon_purges_instead_of_greeting():
    src = (PKG / "daemon.py").read_text()
    assert "camera.purge_face_data(brain.state_dir)" in src and "camera.start(" not in src

def test_nothing_on_jarvis_os_opens_a_webcam():
    """No code path asks OpenCV (or anything) for a live camera: VideoCapture is only ever given a file path."""
    assert not (PKG / "core" / "camera.py").exists()
    for p in PKG.rglob("*.py"):
        t = p.read_text(errors="replace")
        for m in re.finditer(r"VideoCapture\(([^)]*)\)", t):
            arg = m.group(1).strip()
            assert not re.fullmatch(r"\d+|-?\d+\s*,.*|self\.cfg.*index.*|\"/dev/video.*", arg), (p.name, arg)
        assert "FaceWatcher" not in t, p.name
