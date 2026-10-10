"""Making code and images (creator.py, makers.py, safety.py), with every download, model and window faked."""
import io, json, os, stat, zipfile
import pytest
from jarvisd import creator, makers, safety, brain as B

def test_parse():
    P = makers.parse
    assert P("Jarvis, make an image of a dragon over a castle") == ("image", {"prompt": "a dragon over a castle"})
    assert P("draw me a robot cat") == ("image", {"prompt": "robot cat"})
    assert P("make another one") == ("image_again", {})
    assert P("make an image") == ("image_what", {})
    assert P("write a python script that renames my screenshots")[0] == "code"
    assert P("code me a calculator in javascript")[0] == "code"
    assert P("open my images") == ("image_folder", {}) and P("open my code") == ("code_folder", {})
    assert P("set up the image engine") == ("maker_status", {"setup": True})
    assert P("what's the zip code of boston") is None and P("what time is it") is None

def test_prompt_safety_is_strict():
    for bad, kind in (("a n u d e person", "adult"), ("a bloody battlefield with corpses", "gore"),
                      ("Taylor Swift riding a dragon", "real_person"), ("a realistic photo of my teacher", "real_person"),
                      ("n4ked elf", "adult")):
        assert safety.check_prompt(bad) == (False, kind), bad
    for ok in ("iron man flying over a city at night", "a red sports car on a race track", "a cute robot dog", "Spider Man on a roof"):
        assert safety.check_prompt(ok) == (True, None), ok
    assert not safety.check_code_request("write a keylogger") and safety.check_code_request("write a python timer")
    assert "nsfw" in safety.NEGATIVE and "fully clothed" in safety.SAFE_SUFFIX

def test_extract_code_and_names():
    code, lang, note = creator.extract_code("Here:\n```python\nprint('hi')\n```\nRun it with python.", "python")
    assert code == "print('hi')\n" and lang == "python" and note == "Run it with python."
    assert creator.guess_lang("a bash script that backs up my saves") == "bash"
    assert creator.is_big_build("make a whole website with a server") and not creator.is_big_build("a small python script")

class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False

def test_code_maker_writes_one_file_and_never_runs_it(tmp_path, monkeypatch):
    sent, shown = [], []
    def opener(rq, timeout=None):
        u = rq.full_url
        if u.endswith("/api/tags"): return FakeResp(json.dumps({"models": [{"name": "qwen2.5-coder:3b"}]}).encode())
        if u.endswith("/api/generate"): return FakeResp(b"{}")
        sent.append(json.loads(rq.data))
        return FakeResp(json.dumps({"message": {"content": "```python\nfor i in range(3):\n    print(i)\n```\nRun: python3 file.py"}}).encode())
    cm = creator.CodeMaker({"assistant": {"local_ai_url": "http://x"}}, out_dir=str(tmp_path), show=lambda k, p: shown.append((k, p)), opener=opener)
    assert cm.have_model()
    path, src, lang, note = cm.write("write a python script that counts to three")
    assert os.path.basename(path) == "counts_to_three.py" and src.startswith("for i") and lang == "python"
    assert not os.stat(path).st_mode & stat.S_IXUSR                     # never executable
    assert sent[0]["model"] == "qwen2.5-coder:3b" and "ONE complete" in sent[0]["messages"][0]["content"]
    assert shown == [("code", {"path": path})]
    path2, *_ = cm.write("write a python script that counts to three")
    assert path2.endswith("counts_to_three_2.py")

class FakeChecker:
    def __init__(self, path, safe=True): self.path, self.safe = path, safe
    def is_safe(self, p): return self.safe, 0.01 if self.safe else 0.97

def _img_maker(tmp_path, safe=True, fail=False):
    home = tmp_path / "gen"; (home / "cpu").mkdir(parents=True)
    (home / "cpu" / "sd-cli").write_text("x"); (home / creator.SD_MODEL[0]).write_text("m")
    chk = FakeChecker(str(home / "nsfw.onnx"), safe); open(chk.path, "w").write("n")
    runs, shown = [], []
    def run(argv, **kw):
        runs.append(argv)
        if fail:
            class R: returncode = 1; stderr = "boom"
            return R()
        open(argv[argv.index("-o") + 1], "wb").write(b"\x89PNG fake")
        class R: returncode = 0; stderr = ""
        return R()
    im = creator.ImageMaker({"assistant": {"local_ai_url": "http://127.0.0.1:9"}}, home=str(home), out_dir=str(tmp_path / "pics"),
                            show=lambda k, p: shown.append((k, p)), run=run, checker=chk, nvidia=False)
    return im, runs, shown

def test_image_maker_saves_safe_images_and_shows_them(tmp_path):
    im, runs, shown = _img_maker(tmp_path)
    assert im.build == "cpu" and im.installed()
    res, path = im.generate("a cute robot dog", seed=7)
    assert res == "ok" and os.path.exists(path) and path.startswith(str(tmp_path / "pics"))
    a = runs[0]
    assert a[a.index("-p") + 1].endswith(safety.SAFE_SUFFIX) and a[a.index("-n") + 1] == safety.NEGATIVE and "--threads" in a
    assert shown == [("image", {"path": path, "prompt": "a cute robot dog"})] and im.last["seed"] == 7

def test_unsafe_result_is_deleted_and_never_shown(tmp_path):
    im, runs, shown = _img_maker(tmp_path, safe=False)
    assert im.generate("a knight") == ("unsafe", None)
    assert shown == [] and not os.listdir(tmp_path / "gen" / "tmp") and not os.path.exists(tmp_path / "pics")

def test_refused_prompt_never_reaches_the_engine(tmp_path):
    im, runs, shown = _img_maker(tmp_path)
    assert im.generate("a nude statue")[0] == "refused" and runs == []

def test_engine_unpack_makes_the_binary_executable(tmp_path):
    z = tmp_path / "e.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("build/bin/sd-cli", "#!/bin/sh\n"); f.writestr("build/bin/libstable-diffusion.so", "x")
    im, *_ = _img_maker(tmp_path)
    im._unpack(str(z), "vulkan")
    exe = tmp_path / "gen" / "vulkan" / "sd-cli"
    assert exe.exists() and os.access(exe, os.X_OK) and not z.exists()

class FakeImg:
    def __init__(self, installed=True): self._i, self.made, self.last, self.build = installed, [], None, "vulkan"; import threading; self.lock = threading.Lock(); self.out_dir = "/tmp"
    def installed(self): return self._i
    def install(self): self._i = True
    def generate(self, p): self.made.append(p); self.last = {"prompt": p}; return "ok", "/tmp/x.png"

class FakeCode:
    def __init__(self, have=True): self.have, self.req = have, []; import threading; self.lock = threading.Lock(); self.out_dir = "/tmp"
    def have_model(self): return self.have
    def ensure_model(self): self.have = True; return True
    def write(self, r): self.req.append(r); return "/tmp/a.py", "print(1)\nprint(2)\n", "python", ""

def _brain(tmp_path, gaming=False, forward=None):
    said = []
    b = B.LinuxBrain({}, state_dir=str(tmp_path), run=lambda argv, **k: (0, ""), say=lambda t: said.append(t) or t, forward=forward)
    b.makers = makers.Makers(b, code=FakeCode(), image=FakeImg(), start=lambda fn: fn())
    b.is_gaming = lambda: gaming
    return b, said

def test_brain_makes_images_and_says_so(tmp_path):
    b, said = _brain(tmp_path)
    b.handle("Jarvis, make an image of a dragon over a castle")
    assert b.makers.image.made == ["a dragon over a castle"] and said[-1].startswith(("Here it is", "Done", "Fresh"))
    b.handle("make another one")
    assert b.makers.image.made[-1] == "a dragon over a castle"
    b.handle("make an image of a nude elf")
    assert len(b.makers.image.made) == 2 and "don't make that" in said[-1] or "Not that one" in said[-1]

def test_no_images_during_a_game(tmp_path):
    b, said = _brain(tmp_path, gaming=True)
    b.handle("make an image of a spaceship")
    assert b.makers.image.made == [] and said[-1].startswith("After your match")

def test_brain_writes_code_but_big_builds_go_to_tab(tmp_path):
    sent = []
    b, said = _brain(tmp_path, forward=sent.append)
    b.handle("write a python script that prints the date")
    assert b.makers.code.req == ["write a python script that prints the date"] and "I haven't run it" in said[-1]
    b.handle("write code for a whole website with a server")
    assert sent and said[-1].startswith("That's a bigger build")
    b.handle("write a keylogger script")
    assert said[-1].startswith("I won't write that")
