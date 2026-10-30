"""JARVIS's web apps on JARVIS OS (1.0.0-rc6; Fly Vault joined in rc7): JARVIS VR (the Quest web app), the aim trainer,
Reality Orbs and Fly Vault (five caged fruit flies, each on its own tiny brain sim, under a simulated-time clock). They run
in any browser, so on the PC they are launchers: an app-menu entry each (/usr/share/applications/jarvis-*-web.desktop),
a JARVIS APPS menu in the workspace panel's Quick launch header, and a voice phrase ("open the aim trainer", "open reality orbs",
"open fly vault", "open jarvis vr"). Nothing here talks to the network; xdg-open hands the address to the default browser.
"""
from __future__ import annotations
import re

APPS = {
    "vr":   {"name": "JARVIS VR",     "url": "https://jarvis-vr.sites.tab.bot/",   "desktop": "jarvis-vr-web.desktop",
             "spoken": "JARVIS VR"},
    "aim":  {"name": "Aim Trainer",   "url": "https://jarvis-aim.sites.tab.bot/",  "desktop": "jarvis-aim-web.desktop",
             "spoken": "the aim trainer"},
    "orbs": {"name": "Reality Orbs",  "url": "https://jarvis-orbs.sites.tab.bot/", "desktop": "jarvis-orbs-web.desktop",
             "spoken": "Reality Orbs"},
    "fly":  {"name": "Fly Vault",     "url": "https://jarvis-fly.sites.tab.bot/",  "desktop": "jarvis-fly-web.desktop",
             "spoken": "Fly Vault"},
}

_OPEN = r"(?:open|launch|start|load|run|bring up|pull up|show me|go to)"


def parse_voice(text):
    """'aim' | 'orbs' | 'fly' | None. ("open jarvis vr" stays with the VR bridge's own phrase, which also shows the address.)"""
    t = (text or "").lower().strip()
    t = re.sub(r"[!?.,]+$", "", t)
    t = re.sub(r"^(?:(?:hey |ok |okay )?jarvis[, ]*)", "", t)
    t = re.sub(r"^(?:please |can you |could you |let's |lets |i want to |go ahead and )+", "", t).strip()
    t = re.sub(r"(?: please| for me| jarvis| now)+$", "", t).strip()
    F = lambda rx: re.fullmatch(rx, t)
    if F(_OPEN + r" (?:the |my )?(?:jarvis )?aim ?(?:trainer|training|lab|practice|coach)(?: app| site)?|"
         r"(?:practi[cs]e|train) (?:my )?aim|aim training"):
        return "aim"
    if F(_OPEN + r" (?:the |my )?(?:jarvis )?(?:reality )?orbs?(?: app| site)?|" + _OPEN + r" (?:the )?reality orbs?|"
         r"(?:turn|change) my room into (?:another|a different) world"):
        return "orbs"
    if F(_OPEN + r" (?:the |my )?(?:jarvis )?(?:fly|flies|flight) ?(?:vault|vaults|fault)(?: app| site)?|" +
         _OPEN + r" (?:the |my )?(?:fruit )?fl(?:y|ies) (?:cages?|sim(?:ulation|ulator)?|tank|lab)|"
         r"(?:check on|show me|visit|feed) (?:the |my )?(?:fruit )?flies(?: in (?:the )?(?:fly )?vault)?"):
        return "fly"
    return None


def open_argv(key):
    a = APPS.get(key)
    return ["xdg-open", a["url"]] if a else None


def say_words(key, sir="sir"):
    a = APPS[key]
    return f"Opening {a['spoken']} in your browser, {sir}."
