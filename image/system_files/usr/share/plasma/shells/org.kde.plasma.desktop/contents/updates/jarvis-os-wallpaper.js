// JARVIS OS: runs once per user when Plasma starts (Plasma tracks it in plasmashellrc) and sets the JARVIS wallpaper.
var all = desktops();
for (var i = 0; i < all.length; i++) {
    var d = all[i];
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    d.writeConfig("Image", "file:///usr/share/wallpapers/JARVIS/");
}
