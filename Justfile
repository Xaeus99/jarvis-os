# Local helpers (need podman for image work; the daemon tests need only Python)
test:
    cd jarvisd && QT_QPA_PLATFORM=offscreen python -m pytest -q tests
art:
    python3 tools/make_art.py image/system_files/usr/share/wallpapers/JARVIS/contents/images 2560 1440
bootdemo:
    bash bootdemo/build.sh out && bash bootdemo/boot_qemu.sh out/jarvis-bootmenu-demo.iso out/bootmenu.png 13
build:
    cp -r jarvisd image/jarvisd && sudo podman build -t localhost/jarvis-os:dev image
switch-local:
    sudo bootc switch --transport containers-storage localhost/jarvis-os:dev
