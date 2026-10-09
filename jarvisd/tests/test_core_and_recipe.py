"""The shared core is byte-identical to Windows v5.1, and the image recipe is internally consistent."""
import hashlib, json, os, re, subprocess
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "jarvisd")
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
IMG = os.path.join(REPO, "image")
SYS = os.path.join(IMG, "system_files")

def test_vendored_core_matches_manifest():
    man = json.load(open(os.path.join(PKG, "core", "VENDORED.json")))
    for name, sha in man["files"].items():
        assert hashlib.sha256(open(os.path.join(PKG, "core", name), "rb").read()).hexdigest() == sha, name

def test_containerfile_base_is_pinned_nvidia_open():
    cf = open(os.path.join(IMG, "Containerfile")).read()
    assert re.search(r"ghcr\.io/ublue-os/bazzite-nvidia-open:stable@sha256:[0-9a-f]{64}", cf)
    assert "bootc container lint" in cf
    for src in re.findall(r"^COPY (\S+) ", cf, re.M):
        if src not in ("jarvisd", "cosign.pub"):   # CI copies jarvisd/ and cosign.pub in before the build
            assert os.path.exists(os.path.join(IMG, src)), src

def test_scripts_parse():
    for p in ["build_files/build.sh", "system_files/usr/libexec/jarvis-os/grub-theme",
              "system_files/usr/libexec/jarvis-os/bootnext-windows"]:
        path = os.path.join(IMG, p)
        assert os.access(path, os.X_OK), p
        assert subprocess.run(["bash", "-n", path]).returncode == 0, p

def test_services_enabled_exist():
    build = open(os.path.join(IMG, "build_files", "build.sh")).read()
    for unit in re.findall(r"([\w-]+\.service)", build):
        hits = [os.path.join(d, unit) for d in ("usr/lib/systemd/user", "usr/lib/systemd/system")
                if os.path.exists(os.path.join(SYS, d, unit))]
        assert hits, unit
        txt = open(os.path.join(SYS, hits[0])).read()
        assert "[Service]" in txt and "ExecStart=" in txt and "[Install]" in txt

def test_service_modules_exist():
    for unit in ("jarvisd.service", "jarvis-indicator.service"):
        mod = re.search(r"-m (jarvisd\.\w+)", open(os.path.join(SYS, "usr/lib/systemd/user", unit)).read()).group(1)
        assert os.path.exists(os.path.join(PKG, mod.split(".")[1] + ".py")), mod

def test_bootnext_helper_matches_polkit_and_brain():
    pol = open(os.path.join(SYS, "usr/share/polkit-1/actions/os.jarvis.bootnext-windows.policy")).read()
    assert "/usr/libexec/jarvis-os/bootnext-windows" in pol and "<allow_any>no</allow_any>" in pol
    assert "/usr/libexec/jarvis-os/bootnext-windows" in open(os.path.join(PKG, "brain.py")).read()
    helper = open(os.path.join(SYS, "usr/libexec/jarvis-os/bootnext-windows")).read()
    assert "--bootnext" in helper and "--bootorder" not in helper and "-o " not in helper

def test_bootnext_helper_against_sample(tmp_path):
    """Run the real helper with a fake efibootmgr on PATH."""
    from conftest import EFI
    fake = tmp_path / "efibootmgr"
    fake.write_text("#!/bin/sh\nif [ \"$1\" = --bootnext ]; then echo \"$2\" > " + str(tmp_path / "next") + "; else cat <<'E'\n" + EFI + "E\nfi\n")
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    out = subprocess.run(["bash", os.path.join(SYS, "usr/libexec/jarvis-os/bootnext-windows")], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and (tmp_path / "next").read_text().strip() == "0000"

def test_grub_theme_fonts_and_images_exist():
    d = os.path.join(SYS, "usr/share/grub/themes/jarvis")
    theme = open(os.path.join(d, "theme.txt")).read()
    names = set()
    for f in os.listdir(d):
        if f.endswith(".pf2"):
            b = open(os.path.join(d, f), "rb").read(256); i = b.find(b"NAME")
            names.add(b[i + 8:b.index(b"\0", i + 8)].decode())
    for used in set(re.findall(r'font = "([^"]+)"', theme)) | set(re.findall(r'(?:item_font|selected_item_font|terminal-font): ?"([^"]+)"', theme)):
        assert used in names, (used, names)
    for img in ["background.png"] + [f"select_{k}.png" for k in ("c", "n", "s", "e", "w", "ne", "nw", "se", "sw")]:
        assert os.path.exists(os.path.join(d, img)), img

def test_color_scheme_has_kde_groups():
    cs = open(os.path.join(SYS, "usr/share/color-schemes/JarvisVibranium.colors")).read()
    for g in ("Colors:Window", "Colors:View", "Colors:Button", "Colors:Selection", "WM"):
        assert f"[{g}]" in cs

def test_kill_switch_shortcut_declared():
    d = open(os.path.join(SYS, "usr/share/applications/jarvis-takeback.desktop")).read()
    assert "Exec=jarvisctl takeback" in d and "X-KDE-Shortcuts=Meta+Shift+Esc" in d

def test_windows_switch_script_matches_python_parser():
    ps = open(os.path.join(REPO, "windows", "Switch-To-JarvisOS.ps1")).read()
    assert "bootsequence" in ps and "{fwbootmgr}" in ps and "JARVIS|bazzite|Fedora" in ps
    assert "displayorder" not in ps.split("param")[1]       # never rewrites the permanent order
