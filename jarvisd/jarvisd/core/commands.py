"""Turns a transcribed sentence into an intent.

Pure Python, no Windows calls, so it can be tested anywhere.
parse(text, app_names) -> Intent(kind, arg, raw)
kinds: qol (v5.1, see jarvis/qol.py), open_app, close_app, media, volume, search, youtube, time, date,
       lock, sleep, pause_listening, overlay_show, overlay_hide, cancel,
       thanks, forward, empty
  v2: stream_start, stream_stop, record_start, record_stop, clip, briefing,
       weather, learn_face, forget_face, camera_on, camera_off, repeat,
       screen_off, shutdown
  v2 is forgiving: if the strict rules miss, it repairs a clipped first word
  ("pen spotify" -> "open spotify", "time is it" -> time), maps synonyms, and
  fuzzy-matches against every local command before anything is forwarded.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

@dataclass
class Intent:
    kind: str
    arg: object = None
    raw: str = ""
    extra: dict = field(default_factory=dict)

_FILLER = re.compile(
    r"^(?:(?:hey|hi|ok|okay|yo)\s+)?(?:jarvis[,.!]?\s*)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+|kindly\s+|i\s+want\s+you\s+to\s+|i\s+need\s+you\s+to\s+|go\s+ahead\s+and\s+)?",
    re.I)

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100, "max": 100, "maximum": 100, "full": 100, "half": 50,
}

def normalize(text: str) -> str:
    t = (text or "").strip()
    t = t.replace("\u2019", "'")
    t = re.sub(r"[\"“”]", "", t)
    t = re.sub(r"\s+", " ", t)
    t = t.strip(" .!?,")
    # whisper sometimes hears the wake word again at the start
    t = re.sub(r"^(?:hey|hi|ok|okay)?\s*j\.?a\.?r\.?v\.?i\.?s\.?[,.!]?\s*", "", t, flags=re.I)
    t = _FILLER.sub("", t, count=1)
    t = re.sub(r"\s+(?:please|for me|right now|now|thanks|thank you)$", "", t, flags=re.I)
    return t.strip(" .!?,")

def _number(s: str):
    s = s.strip().lower().replace("%", "").replace("percent", "").strip()
    if s.isdigit():
        return int(s)
    total = 0
    found = False
    for w in re.split(r"[\s-]+", s):
        if w in NUMBER_WORDS:
            total += NUMBER_WORDS[w]; found = True
    return total if found else None

def _match_app(name: str, app_names):
    """Best known app for a spoken name, else None (caller can try Start menu)."""
    n = re.sub(r"^(?:the|my|up)\s+", "", name.lower()).strip(" .")
    n = re.sub(r"\s+(?:app|application|program|launcher)$", "", n).strip()
    if not n:
        return None, n
    keys = {k.lower(): k for k in app_names}
    if n in keys:
        return keys[n], n
    # aliases declared as "name|alias|alias"
    for k in app_names:
        for alias in k.lower().split("|"):
            if alias.strip() == n:
                return k, n
    compact = n.replace(" ", "")
    for k in app_names:
        for alias in k.lower().split("|"):
            if alias.replace(" ", "") == compact:
                return k, n
    for k in app_names:  # "open google chrome" -> chrome
        for alias in k.lower().split("|"):
            if re.search(r"\b" + re.escape(alias.strip()) + r"\b", n):
                return k, n
    return None, n

MEDIA = [
    (r"^(?:pause|stop)(?: the)?(?: music| song| track| spotify| playback| video)?$", "play_pause"),
    (r"^(?:play|resume|unpause|continue)(?: the)?(?: music| song| track| spotify| playback| video)?$", "play_pause"),
    (r"^(?:play[/ ]pause|toggle (?:music|playback))$", "play_pause"),
    (r"^(?:next|skip)(?: (?:song|track|one|this(?: song)?))?$", "next"),
    (r"^(?:skip|play) (?:to )?(?:the )?next(?: (?:song|track|one))?$", "next"),
    (r"^(?:previous|last|go back)(?: (?:song|track|one))?$", "previous"),
    (r"^(?:play )?(?:the )?previous(?: (?:song|track|one))?$", "previous"),
    (r"^(?:go back a (?:song|track)|back one (?:song|track))$", "previous"),
]

MUSIC = r"(?:the |my |some |a )?(?:music|song|songs|track|tunes|spotify|playback|video|playlist|beat|beats)"

SMALL_TALK = [
    (r"(?:hello|hi|hey|yo|hiya|howdy|heya|sup|wassup|what'?s up|whats up|what is up|good (?:morning|afternoon|evening|day)|"
     r"morning|evening|how are you(?: doing| today)?|how'?s it going|how are things|you good|how do you do|greetings)", "greet"),
    (r"(?:bye|bye bye|goodbye|good bye|see (?:you|ya)(?: later| soon| tomorrow)?|later|catch you later|cya|ttyl|peace(?: out)?|"
     r"i'?m (?:heading|going) out|i'?m leaving|i'?m off|i'?ve got to go|i gotta go|gotta go|talk (?:to you )?later)", "farewell"),
    (r"(?:yes|yeah|yep|yup|yes please|ok|okay|sure|do it|confirm(?:ed)?|affirmative|go ahead|sounds good|please do|correct|absolutely|definitely)", "affirm"),
    (r"(?:who are you|what are you|what'?s your name|what is your name|introduce yourself)", "who_are_you"),
    (r"(?:are you there|you there|are you awake|are you listening|are you on|you awake|jarvis)", "are_you_there"),
    (r"(?:i love you|love you|love ya|you'?re the best|you are the best|you'?re awesome|you are awesome)", "love"),
    (r"(?:tell me a joke|tell me another(?: one| joke)?|another joke|say something funny|make me laugh|joke|got any jokes|know any jokes)", "joke"),
    (r"(?:good job|nice|nice one|nice work|well done|great job|great work|awesome|perfect|sick|lets go|let'?s go|w|big w|goated)", "praise"),
    (r"(?:thanks?|thank you|thank you so much|thanks a lot|appreciate it|i appreciate (?:it|you)|cheers|ty|thx|much appreciated)(?: jarvis)?", "thanks"),
]
SMALL_CANON = {"hello": "greet", "good morning": "greet", "how are you": "greet", "what's up": "greet",
               "goodbye": "farewell", "see you later": "farewell", "i'm heading out": "farewell",
               "yes": "affirm", "yeah": "affirm", "do it": "affirm", "who are you": "who_are_you",
               "are you there": "are_you_there", "i love you": "love", "tell me a joke": "joke",
               "good job": "praise", "thank you": "thanks", "appreciate it": "thanks"}


# v4 (Oct 9): the workspace window, status report, focus mode, colour themes, sound effects
THEME_WORDS = r"(?:arc cyan|arc reactor|arc|cyan|blue|light blue|teal|classic|default|normal|mark (?:3|iii|three)|mark3|red and gold|red|gold|iron ?man|stealth|black and white|black|white|grey|gray|silver|minimal|dark|vibranium|black panther|panther|wakanda|purple|violet|pink|hulk ?buster|hulk|orange|amber|yellow|ultron|crimson|evil|dark red|blood red)"
V4_RULES = [
    (r"(?:open|show|launch|bring up|pull up|start|display|activate|load)(?: up)? (?:the |my |your )?(?:work ?space|dashboard|interface|command (?:center|centre)|control (?:panel|center|centre)|hud workspace|app|ui)(?: up)?"
     r"|work ?space(?: mode)?(?: on)?|(?:show me )?(?:the |my )?(?:work ?space|dashboard)", "workspace_open"),
    (r"(?:close|hide|minimi[sz]e|exit|dismiss|shut|put away)(?: down)? (?:the |my |your )?(?:work ?space|dashboard|interface|command (?:center|centre)|control (?:panel|center|centre)|app|ui)(?: down)?|work ?space off", "workspace_close"),
    (r"(?:give me |run |do |full |quick )?(?:a |the |my )?(?:status report|status update|sitrep|system(?:s)? (?:status|report|check)|diagnostics?|(?:pc|computer) status)"
     r"|how(?:'s| is| are) (?:my |the )?(?:pc|computer|system|systems|rig) (?:doing|running|looking)|status", "status_report"),
    (r"(?:(?:end|stop|exit|cancel|disable|finish|leave|turn off|kill|quit)(?: the)? focus(?: mode| session| timer)?|focus mode off|(?:i'?m )?done focusing)", "focus_stop"),
    (r"(?:(?:start|enter|activate|engage|begin|enable|turn on|initiate|go into)(?: the| a)? )?focus(?: mode| session| timer)(?: on)?(?: for .+)?|help me focus(?: for .+)?|i need to focus(?: for .+)?|lock in mode|time to lock in", "focus_start"),
    (r"(?:(?:switch|change|set|go|turn)(?: (?:the|your|my))? (?:theme|colou?rs?|hud|look)(?: to| into)? )" + THEME_WORDS + r"(?: (?:theme|mode|colou?rs?|look|skin))?"
     r"|" + THEME_WORDS + r" (?:theme|mode|colou?rs?|look|skin)"
     r"|(?:use|switch to|go|change to|turn) (?:the )?" + THEME_WORDS + r" (?:theme|mode|colou?rs?|look|skin)|(?:next|change|switch|cycle)(?: the)? (?:theme|colou?rs?)", "theme"),
    (r"(?:turn |switch )?(?:the )?(?:sound effects|sounds|sfx|beeps|chimes|bleeps)(?: to)? (?:on|off)|(?:mute|disable|turn off|stop|silence|enable|turn on|unmute)(?: the| your)? (?:sound effects|sfx|beeps|chimes|bleeps)", "sounds"),
]


# v5 (Oct 9): JARVIS itself (exit / standby / wake), dismissing the HUD, the message log, Copilot
JARVIS_WORD = r"(?:jarvis|j a r v i s|yourself|the assistant)"
V5_RULES = [
    # "close the app" hides the workspace window; JARVIS keeps listening in the tray (v4 rules cover app/interface/ui)
    (r"(?:hide|minimi[sz]e|put away|dismiss) (?:jarvis|the jarvis (?:app|window|interface)|your window|the window|the interface|the app)", "workspace_close"),
    (r"(?:show|open|bring up|pull up|bring back|unhide) (?:jarvis|the jarvis (?:app|window|interface)|your window|the window|the interface|the app)", "workspace_open"),
    (r"(?:close|exit|quit|shut ?down|shut off|turn off|kill|terminate|power down|switch off|end)(?: down)? " + JARVIS_WORD + r"(?: down| off| now)?"
     r"|(?:goodbye|good bye|bye|bye bye|see you|see ya|later|farewell),? jarvis"
     r"|jarvis,? (?:shut ?down|exit|quit|close|power down|turn off|goodbye|bye)", "jarvis_exit"),
    (r"(?:(?:you can |please )?go to sleep|go to standby|standby(?: mode)?|stand ?by(?: mode)?|take a nap|go dormant|sleep,? jarvis|jarvis,? (?:go to )?sleep|(?:go to )?sleep jarvis|nap time)", "jarvis_sleep"),
    (r"(?:wake up|wakey wakey|rise and shine|(?:i'?m|i am) back|come back online|back online|you can wake up|wake up jarvis|jarvis wake up)", "jarvis_wake"),
    (r"(?:close|close it|close that|close this|dismiss|go away|hide|bye[ -]?bye|close,? bye[ -]?bye|close,? goodbye|ok close|okay close)", "dismiss"),
    (r"(?:open|show|display|pull up|bring up|read|check|view|see)(?: me)? (?:my |our |the |your )?(?:conversation |message |chat |command )?(?:log|logs|history|message history|chat history|conversation history|messages)"
     r"|(?:show|open) (?:me )?our (?:messages|conversation|conversations|chats?|chat log)|(?:message|chat|conversation) (?:log|history)", "open_log"),
    (r"(?:open|launch|start|bring up|pull up|show)(?: up)? (?:windows |microsoft )?copilot", "copilot_open"),
    (r"(?:what (?:am i|are we) (?:looking at|seeing|watching)(?: right now| here)?|what(?:'s| is) (?:on )?(?:my |the |this )screen(?: right now)?|"
     r"what(?:'s| is) (?:this|that) on (?:my|the) screen|explain (?:this|that|my screen|the screen|what i'?m (?:looking at|seeing))(?: to me)?(?: please)?|"
     r"help me with (?:this|that|what'?s on (?:my|the) screen)|(?:take a )?look at (?:my|the|this) screen|can you see (?:my|the|this) screen|read (?:my|the) screen|"
     r"what do you (?:see|think of this)|what is this)", "screen_explain"),
    (r"(?:ask|tell|have|get) (?:windows )?copilot(?: to| about| for| if| whether| what| how| why| when| where| who)? .+", "copilot_ask"),
    (r"(?:list|show|tell me|read)(?: me)?(?: my| the| all)?(?: available)? (?:audio|sound|output|speaker|playback) (?:devices|outputs|options)|what (?:audio|sound|output|playback) devices (?:do i have|are there|are connected)|audio devices", "audio_list"),
    (r"(?:use|switch to|go back to|reset to|change to)(?: the)? (?:default|system default|windows default|normal)(?: audio| sound| output| device| speaker| speakers)?(?: device)?|reset (?:the )?audio(?: device| output)?", "audio_default"),
    (r"(?:play|use|talk|speak) (?:on |through |out of )?(?:all|every|both)(?: of)?(?: the| my)? (?:devices|speakers|outputs|audio devices)|mirror (?:the )?(?:audio|sound|voice)(?: on| off)?|(?:stop|turn off|disable) (?:audio |sound )?mirroring", "audio_mirror"),
    (r"(?:use|switch to|change to|go to|swap to|switch over to|put it on)(?: my| the)? (?:speakers?|headphones?|headset|earbuds|airpods|buds|desk speakers|monitor speakers)(?: instead)?"
     r"|(?:switch|change|set|move|send|put) (?:the |your )?(?:audio|sound|output|voice)(?: output)? (?:to|on) (?:my |the )?.+"
     r"|(?:talk|speak) (?:on|through|out of) (?:my |the )?.+|play (?:your voice|the audio|audio|sound|it|jarvis) (?:on|through|out of) (?:my |the )?.+", "audio_device"),
    (r"(?:help|help me|what can you do|what (?:else )?can you do|what can i say|what (?:commands|things) (?:can i say|do you know|are there|do you have)|list (?:your |the |all )?commands|(?:show|give) me (?:the |your )?commands|commands|how do i use you)(?: jarvis)?", "help"),
]

V3_RULES = [
    # "open my clips": the newest clip (OBS replay / Game Bar capture / recording)
    (r"(?:open|show|play|watch|pull up|bring up|find|get|load)(?: me)? (?:my |the |that |his )?(?:last |latest |newest |most recent |recent |new )?(?:clip|clips|replay|replays|capture|captures|recording)(?: (?:i|you|we) (?:just )?(?:saved|made|clipped|recorded|took))?"
     r"|(?:find|open|show|play)(?: me)? (?:the |that )?(?:video|clip|recording) (?:that )?(?:you|i) just (?:saved|clipped|recorded|took)"
     r"|where(?:'s| is) (?:my |the |that )?(?:last |latest )?clip|(?:show|play) (?:me )?(?:that|the) clip", "open_clips"),
    (r"(?:open|show)(?: me)? (?:my |the )?(?:clips?|captures?|recordings?) folder", "clips_folder"),
]

V2_RULES = V5_RULES + V4_RULES + V3_RULES + [
    # stream / record (before media, so "stop stream" isn't "stop")
    (r"(?:start|begin|launch|kick off|open|enter|activate|turn on)(?: up)?(?: the| my| a)? ?stream(?:ing)?(?: mode)?|stream mode(?: on)?|go live|let'?s (?:go live|stream)|time to stream|i'?m going live", "stream_start"),
    (r"(?:stop|end|finish|kill|exit|close|turn off|wrap up)(?: the| my)? ?stream(?:ing)?(?: mode)?|stream mode off|go offline|(?:i'?m )?done streaming|end the live", "stream_stop"),
    (r"(?:start|begin) (?:a )?(?:recording|record)(?: (?:this|my screen|gameplay|the screen))?|record (?:this|my screen|gameplay|the screen)|hit record", "record_start"),
    (r"(?:stop|end|finish) (?:the )?(?:recording|record)", "record_stop"),
    (r"(?:clip|clipped|clipping) (?:that|it|this)(?: .*)?|clip(?: that)?|(?:save|grab|get|take) (?:a |that |the |this )?(?:clip|replay)(?: .*)?|save (?:that|the last 30 seconds)|that was (?:sick|insane|crazy),? clip (?:it|that)", "clip"),
    # briefing / weather
    (r"(?:(?:give me|read me|start|do|run)(?: the| my)? )?(?:daily |morning |evening )?briefing|brief me|what(?:'s| is| does) (?:my day|today) (?:look like|looking like)|what(?:'s| is) on (?:today|my schedule|my calendar)|what do i have today|morning report|daily report", "briefing"),
    (r"(?:what(?:'s| is| does)? )?(?:the )?weather(?: like)?(?: (?:today|now|outside|right now|tomorrow|look like))?|(?:is it|will it|is it going to|gonna) (?:rain|snow|be cold|be hot)(?: .*)?|how (?:hot|cold|warm) is it(?: outside)?|(?:what's |what is )?(?:the )?temperature(?: outside)?|do i need a (?:jacket|coat|umbrella)", "weather"),
    # screen / power (Oct 9 feedback: goodnight's sleep was confusing; screen-off is lighter)
    (r"(?:turn|switch|shut|put) (?:off )?(?:the |my )?(?:screen|screens|monitor|monitors|display)s?(?: off| to sleep)?|(?:screen|monitor|display)s? off|blank (?:the )?screen|(?:kill|cut) (?:the )?screen", "screen_off"),
    (r"(?:shut ?down|power (?:off|down))(?: (?:the|my))?(?: pc| computer| system| everything)?(?: now)?|(?:turn|switch) off (?:the |my )?(?:pc|computer)|shut (?:the |my )?(?:pc|computer) (?:down|off)", "shutdown"),
    # HUD self-test
    (r"(?:test|check|try|demo|preview)(?: (?:the|my|your))? (?:overlay|hud|ring|display)|(?:overlay|hud|ring) test", "overlay_test"),
    # camera / face
    (r"(?:learn|remember|scan|register|save|memorize|enroll) my face|learn who i am|(?:learn|remember) me", "learn_face"),
    (r"(?:forget|delete|erase|remove) my face(?: data)?", "forget_face"),
    (r"(?:turn on|enable|start) (?:the )?(?:camera|webcam|face (?:greeting|detection|recognition)|greeting)", "camera_on"),
    (r"(?:turn off|disable|stop) (?:the )?(?:camera|webcam|face (?:greeting|detection|recognition)|greeting)", "camera_off"),
    # repeat the most recent answer (local or from Tab)
    (r"(?:say that again|repeat(?: that| it| the last (?:one|message|reply))?|what did (?:you|tab) (?:just )?say|come again|read (?:that|it) again|(?:read|what was) (?:the |tab's )?last (?:message|reply))", "repeat"),
    # exact echo: "repeat after me <words>" / "say <words>"; after repeat to avoid stealing "say that again"
    (r"(?:repeat after me|say) .+", "echo"),
]

# every phrasing that should mean play/pause (the "Unpause my music" miss)
PLAY_PAUSE = re.compile(
    r"(?:(?:un ?pause|unpause|resume|continue|play|start|restart|pause|stop|toggle)(?: playing)?(?: " + MUSIC + r")?(?: again| back| up)?"
    r"|(?:put|turn) (?:on )?(?:the |my |some )?(?:music|tunes|spotify)(?: back on| on)?"
    r"|(?:music|song|spotify) (?:on|off|back on|pause|play)|(?:my |the )?(?:music|tunes)"
    r"|(?:hit|press) (?:play|pause)|play[/ ]pause|toggle (?:music|playback))")

TIME_WORDS = {"what", "what's", "whats", "is", "it", "the", "time", "current", "tell", "me", "please", "now",
              "right", "do", "you", "know", "have", "got", "check", "say", "s", "a", "clock", "o'clock", "rn"}

def _is_time_question(low):
    words = re.findall(r"[a-z']+", low)
    if "time" not in words or len(words) > 7:
        return False
    if re.search(r"(?:timer|remind|reminder|schedule|alarm|until|till|left|zone|in (?:\w+ )?(?:london|tokyo|japan|uk|california|la))", low):
        return False
    return all(w in TIME_WORDS for w in words)

def _parse_v2(low):
    for pat, kind in V2_RULES:
        if re.fullmatch(pat, low):
            return kind
    return None

# clipped or misheard first words -> the verb they probably were
VERBS = ["open", "launch", "start", "close", "quit", "pause", "unpause", "play", "resume", "stop", "skip", "next",
         "previous", "search", "lock", "mute", "unmute", "volume", "clip", "record", "set", "turn", "show", "hide",
         "what's", "what", "google", "look"]
CLIPPED = {"pen": "open", "pin": "open", "oben": "open", "opin": "open", "opun": "open", "upen": "open",
           "aunch": "launch", "unch": "launch", "lunch": "launch", "lose": "close", "los": "close", "lows": "close",
           "clothes": "close", "cloves": "close", "uit": "quit", "ause": "pause", "paws": "pause", "pose": "pause",
           "pods": "pause", "npause": "unpause", "lay": "play", "ay": "play", "esume": "resume", "zoom": "resume",
           "kip": "skip", "ext": "next", "nex": "next", "earch": "search", "urch": "search", "ock": "lock",
           "log": "lock", "ute": "mute", "lip": "clip", "flip": "clip", "lit": "clip", "tart": "start",
           "art": "start", "top": "stop", "olume": "volume", "lume": "volume", "atch": "what's", "hat": "what",
           "hat's": "what's", "ats": "what's", "uts": "what's"}

def _repairs(low):
    """Candidate rewrites of a sentence whose first word got clipped or misheard."""
    words = low.split()
    if not words:
        return []
    first, rest = words[0], " ".join(words[1:])
    out = []
    if first in CLIPPED:
        out.append((CLIPPED[first] + " " + rest).strip())
    for v in VERBS:  # "pen" is the tail of "open"; "opan" is close to "open"
        if len(first) >= 2 and v != first and v.endswith(first) and len(v) - len(first) <= 2:
            out.append((v + " " + rest).strip())
    import difflib
    for v in difflib.get_close_matches(first, VERBS, n=2, cutoff=0.75):
        if v != first:
            out.append((v + " " + rest).strip())
    if rest:  # an extra junk word at the front ("uh open spotify", "and pause")
        out.append(rest)
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c); uniq.append(c)
    return uniq

# canonical phrases for the last-chance fuzzy match (typos and near-misses)
CANON = {
    "what time is it": "time", "what's the date": "date", "lock the pc": "lock", "goodnight": "sleep",
    "pause the music": "media:play_pause", "play the music": "media:play_pause", "unpause the music": "media:play_pause",
    "next song": "media:next", "skip this song": "media:next", "previous song": "media:previous",
    "volume up": "volume:up", "volume down": "volume:down", "mute": "volume:mute",
    "start stream": "stream_start", "stop stream": "stream_stop", "start recording": "record_start",
    "stop recording": "record_stop", "clip that": "clip", "daily briefing": "briefing", "what's the weather": "weather",
    "learn my face": "learn_face", "say that again": "repeat", "stop listening": "pause_listening",
    "hide the hud": "overlay_hide", "show the hud": "overlay_show",
    "turn off the screen": "screen_off", "screen off": "screen_off", "shut down the pc": "shutdown",
    "open my clips": "open_clips", "open my last clip": "open_clips",
    "test overlay": "overlay_test",
}

def _intent_from_canon(tag, raw):
    if tag.startswith("media:"):
        return Intent("media", tag.split(":")[1], raw=raw)
    if tag.startswith("volume:"):
        a = tag.split(":")[1]
        return Intent("volume", (a, None if a == "mute" else 10), raw=raw)
    return Intent(tag, raw=raw)

# "never mind" and how Whisper mishears it ("Keep in mind" on Oct 9). Never forwarded.
CANCEL_RE = (r"(?:never ?mind|nevermind|never mine|keep in mind|cancel(?: that| it)?|nothing|forget (?:it|that|about it)|"
             r"stop|don'?t|do not|don'?t do it|no don'?t|stop listening for now|no thanks?|nah|nope|no|that'?s (?:all|it)|ignore (?:that|me)|scratch that|"
             r"actually never ?mind|oh never ?mind|wrong button|"
             r"that (?:was ?n'?t|wasn'?t|was not|is ?n'?t|isn'?t|is not) (?:for|to|meant for) you|(?:i was ?n'?t|i wasn'?t|i was not|i'?m not) talking to you|not (?:talking to )?you|not for you|wasn'?t for you|"
             r"ignore (?:that|it|this|me)|disregard(?: that| it)?)")
CANCEL_WORDS = ["never mind", "nevermind", "keep in mind", "cancel", "forget it", "nothing", "stop", "scratch that", "that wasn't for you", "not for you", "ignore that"]

def _is_cancel_fuzzy(low):
    import difflib
    if len(low.split()) > 3:
        return False
    return bool(difflib.get_close_matches(low, CANCEL_WORDS, n=1, cutoff=0.78))

def parse(text: str, app_names=(), fuzzy=True, accept=86.0, ask=72.0) -> Intent:
    raw = (text or "").strip()
    t0 = normalize(raw)
    t = normalize(apply_aliases(t0))          # v5: "co-pilot" -> "copilot"
    low = t.lower()
    if not low:
        return Intent("empty", raw=raw)
    if cancel_suffix(low) and not re.fullmatch(CANCEL_RE, low):
        return Intent("cancel", raw=raw, extra={"suffix": True})
    it = _parse(t, low, raw, app_names)
    if it.kind == "forward":
        it.arg = t0 or raw                    # Tab gets what he said, not our rewrite
        if fuzzy:
            ph, sc = fuzzy_command(low, app_names)
            if ph and sc >= accept:
                it2 = _parse(ph, ph, raw, app_names)
                if it2.kind != "forward":
                    it2.extra.update({"fuzzy": ph, "score": sc})
                    return it2
            elif ph and sc >= ask:
                return Intent("clarify", ph, raw=raw, extra={"score": sc, "heard": t0})
    return it

def _parse(t, low, raw, app_names):
    it = _parse_strict(t, raw, app_names)
    if it.kind == "forward" and _is_cancel_fuzzy(low):
        return Intent("cancel", raw=raw, extra={"fuzzy": "cancel"})
    if it.kind == "forward" and len(low.split()) <= 4:
        import difflib
        hit = difflib.get_close_matches(low, list(SMALL_CANON), n=1, cutoff=0.8)
        if hit:
            return Intent(SMALL_CANON[hit[0]], raw=raw, extra={"fuzzy": hit[0]})
    if it.kind != "forward":
        return it
    # forgiving pass: clipped first word, then fuzzy match, before forwarding
    for cand in _repairs(low):
        it2 = _parse_strict(cand, raw, app_names)
        if it2.kind == "open_app" and not it2.extra.get("known"):
            continue  # don't open random Start-menu things on a guess
        if it2.kind == "close_app" and not it2.extra.get("known"):
            continue
        if it2.kind == "qol" and it2.extra.get("destructive"):
            continue  # v5.1: never restart / sign out / empty the bin on a repaired guess
        if it2.kind not in ("forward", "empty", "search", "youtube"):
            it2.extra["repaired"] = cand
            return it2
    import difflib
    best = difflib.get_close_matches(low, list(CANON), n=1, cutoff=0.82)
    if best:
        it3 = _intent_from_canon(CANON[best[0]], raw)
        it3.extra["fuzzy"] = best[0]
        return it3
    return it

def _parse_strict(t: str, raw: str, app_names=()) -> Intent:
    low = t.lower().strip(" .!?,")
    if not low:
        return Intent("empty", raw=raw)

    kind = _parse_v2(low)
    if kind:
        return Intent(kind, raw=raw)

    if re.fullmatch(CANCEL_RE, low):
        return Intent("cancel", raw=raw)
    for pat, kind in SMALL_TALK:
        if re.fullmatch(pat, low):
            return Intent(kind, raw=raw)

    # v5.1: quality-of-life commands JARVIS does himself (timers, files, system status, windows...)
    from . import qol as _qol
    qi = _qol.parse(low, app_names)
    if qi is not None:
        qi.raw = raw
        return qi


    # time / date
    if _is_time_question(low):
        return Intent("time", raw=raw)
    if re.search(r"\bwhat(?:'s| is)? (?:the |today's )?date\b|\bwhat day is (?:it|today)\b", low):
        return Intent("date", raw=raw)

    # lock / sleep
    if re.fullmatch(r"(?:lock(?: (?:the|my))?(?: pc| computer| screen| it)?(?: up)?|lock it down)", low):
        return Intent("lock", raw=raw)
    if re.fullmatch(r"(?:good ?night|goodnight jarvis|night night|(?:put )?(?:the |my )?(?:pc|computer) to sleep|sleep(?: mode)?|go to sleep|shut ?down for the night)", low):
        return Intent("sleep", raw=raw)

    # listening / overlay controls
    if re.fullmatch(r"(?:stop|pause) listening|go quiet|mute yourself|take a break", low):
        return Intent("pause_listening", raw=raw)
    if re.search(r"\b(?:hide|close|turn off) (?:the |your )?(?:overlay|hud)\b", low):
        return Intent("overlay_hide", raw=raw)
    if re.search(r"\b(?:show|open|turn on) (?:the |your )?(?:overlay|hud)\b", low):
        return Intent("overlay_show", raw=raw)

    # volume
    m = re.fullmatch(r"(?:set |turn |put )?(?:the )?volume (?:to |at )?(.+?)(?: percent| %)?", low)
    if m and _number(m.group(1)) is not None:
        return Intent("volume", ("set", max(0, min(100, _number(m.group(1))))), raw=raw)
    if re.fullmatch(r"(?:un ?mute|mute|silence)(?: the)?(?: volume| sound| audio| pc| computer| it)?", low):
        return Intent("volume", ("mute", None), raw=raw)
    if re.search(r"\b(?:volume up|turn (?:it|the volume|the music|this)? ?up|louder|crank it|raise (?:the )?volume|increase (?:the )?volume)\b", low):
        big = bool(re.search(r"\b(?:a lot|way|max|crank)\b", low))
        return Intent("volume", ("up", 20 if big else 10), raw=raw)
    if re.search(r"\b(?:volume down|turn (?:it|the volume|the music|this)? ?down|quieter|softer|lower (?:the )?volume|decrease (?:the )?volume)\b", low):
        big = bool(re.search(r"\b(?:a lot|way)\b", low))
        return Intent("volume", ("down", 20 if big else 10), raw=raw)

    # media keys
    m = re.fullmatch(r"play (.+)", low)
    if m and not re.fullmatch(MUSIC, m.group(1)):
        key, _sp = _match_app(m.group(1), app_names)
        if key and not key.lower().startswith(("spotify", "music", "youtube")):
            return Intent("open_app", key, raw=raw, extra={"known": True, "spoken": m.group(1)})
    if PLAY_PAUSE.fullmatch(low):
        return Intent("media", "play_pause", raw=raw)
    for pat, action in MEDIA:
        if re.fullmatch(pat, low):
            return Intent("media", action, raw=raw)

    # web search
    m = re.fullmatch(r"(?:search|look up|google)(?: (?:on )?youtube| youtube) (?:for )?(.+)", low)
    if m:
        return Intent("youtube", t[-len(m.group(1)):], raw=raw)
    m = re.fullmatch(r"(?:search|look up|google|find) (.+?) on youtube", low)
    if m:
        return Intent("youtube", t[low.index(m.group(1)):low.index(m.group(1)) + len(m.group(1))], raw=raw)
    m = re.fullmatch(r"(?:search(?: the web| google| online)?(?: for)?|google|look up|search up) (.+)", low)
    if m:
        start = len(low) - len(m.group(1))
        return Intent("search", t[start:], raw=raw)

    # open / close apps
    m = re.fullmatch(r"(?:open|launch|start|run|load|boot up|fire up|pull up|bring up)(?: up)? (.+)", low)
    if m:
        key, spoken = _match_app(m.group(1), app_names)
        if key or len(spoken.split()) <= 3:
            return Intent("open_app", key or spoken, raw=raw, extra={"known": bool(key), "spoken": spoken})
    m = re.fullmatch(r"(?:close|quit|exit|kill|shut down|shut|end) (.+)", low)
    if m:
        key, spoken = _match_app(m.group(1), app_names)
        if key or len(spoken.split()) <= 3:
            return Intent("close_app", key or spoken, raw=raw, extra={"known": bool(key), "spoken": spoken})

    return Intent("forward", t or raw, raw=raw)

END_RE = (r"(?:that'?s (?:all|it|everything)(?: for now)?(?: thanks?| thank you)?|that is all|nothing else|no(?:pe|thing)? (?:that'?s|that is) (?:all|it)|"
          r"(?:i'?m|i am|we'?re|we are) (?:good|done|all good|all set)(?: now| thanks?| thank you)?|all (?:good|set|done)|done|we'?re finished|"
          r"no(?: thanks?| thank you)?|nope|nah|thanks?(?: jarvis)?,? (?:that'?s|that is) (?:all|it)|bye|goodbye|see ya|later)")

def is_end_of_conversation(text: str) -> bool:
    """Ends the follow-up chain: "that's all", "I'm good", a cancel word, a goodbye."""
    low = normalize(text).lower().strip(" .!?,")
    if not low:
        return True
    return bool(re.fullmatch(END_RE, low) or re.fullmatch(CANCEL_RE, low))

def is_yes(text: str) -> bool:
    low = normalize(text).lower()
    return bool(re.search(r"\b(?:yes|yeah|yep|yup|sure|do it|confirm(?:ed)?|affirmative|go ahead|please do|correct|ok(?:ay)?)\b", low)) and not is_no(text)

def is_no(text: str) -> bool:
    low = normalize(text).lower()
    return bool(re.search(r"\b(?:no|nope|nah|cancel|don't|do not|stop|never ?mind|negative|wait)\b", low))


# =====================================================================================
# v5 (Oct 9, 2026): fewer false captures, fewer mishearings.
#   ALIASES   - phrase-level fixes for how Whisper mishears his vocabulary 
#   fuzzy     - near-misses against the local command list; a confident hit runs, a so-so hit asks
#               "Did you mean X?" instead of forwarding junk to Tab
#   screen()  - the gate in front of Tab: filler, too-short and low-confidence transcripts are dropped
# =====================================================================================

ALIASES = [
    (r"\bfocus (?:mod|mold|moat|mowed|mo|load|road)\b", "focus mode"),
    (r"\bfocused mode\b", "focus mode"),
    (r"\b(?:terr?a ?r(?:ia|ea|ear|ii?a|ya)|terrarium|tara ?ria|terror ?ia|terrar ?ia)\b", "terraria"),
    (r"\bwall ?paper (?:engine|engines|and gin|in gin|engin|ending)\b", "wallpaper engine"),
    (r"\bwall paper\b", "wallpaper"),
    (r"(?<![a-z])(?:o\.? ?b\.? ?s\.?|oh b s|o bs|ob s|o b s)(?![a-z])", "OBS"),
    (r"\b(purple|violet|red|cyan|blue|ultron|stealth|hulkbuster|hulk buster|vibranium|mark (?:3|iii|three)|next|gold|pink)\s+(?:team|theme's|teams|thing|steam|seem)\b", r"\1 theme"),
    (r"\bwork ?spaces?\b|\bworks ?base\b|\bwork place\b", "workspace"),
    (r"\bstatus (?:reports|rapport|record)\b|\bstate us report\b", "status report"),
    (r"\bco[\s-]?pilots?\b|\bco-pilots?\b|\bcopilots\b", "copilot"),
    (r"\bdis ?cord\b", "discord"),
    (r"\b(close|open|hide|show|clothes) the (?:up|ap|apps|happ|opp|hap)$", r"\1 the app"),
    (r"\bwhat am i (?:looking ad|looking app|lookin at|look at|looking that)\b", "what am i looking at"),
    (r"\bwhat'?s on my (?:scream|screams|green|screen's)\b", "what's on my screen"),
    (r"\bspot ?if(?:y|ie|ai)\b", "spotify"),
    (r"\broblocks\b|\brow blocks\b", "roblox"),
]
_ALIASES = [(re.compile(p, re.I), r) for p, r in ALIASES]

def apply_aliases(text: str) -> str:
    """Rewrite known mishearings of his vocabulary. Safe to run on anything (it only fixes phrases)."""
    t = text or ""
    for rx, rep in _ALIASES:
        t = rx.sub(rep, t)
    return t

# The phrases fuzzy matching compares against. Each maps to the text that parse() then runs.
FUZZY_PHRASES = [
    "focus mode", "end focus mode", "status report", "open workspace", "close workspace",
    "purple theme", "next theme", "red theme", "cyan theme", "ultron theme", "stealth theme", "hulkbuster theme",
    "sound effects off", "sound effects on", "open my clips", "open my clips folder", "clip that", "start stream", "stop stream",
    "start recording", "stop recording", "what time is it", "what's the date", "what's the weather", "daily briefing",
    "say that again", "next song", "previous song", "pause the music", "play the music", "volume up", "volume down", "mute",
    "test overlay", "show the hud", "hide the hud", "lock the pc", "turn off the screen", "learn my face",
    "close jarvis", "goodbye jarvis", "go to sleep", "wake up", "never mind", "that wasn't for you",
    "open my log", "show our messages", "open copilot", "stop listening",
    "what am i looking at", "what's on my screen", "explain this", "help me with this",
    "use my speakers", "switch to headphones", "list audio devices", "what can you do",
    "close the app", "open the app", "hide jarvis", "show jarvis",
]
# leading words that don't change which command it is 
_LEAD = re.compile(r"^(?:(?:activate|initiate|engage|enable|start|turn on|run|do|go|put on|switch on|set)\s+)", re.I)

def _ratio(a, b):
    try:
        from rapidfuzz import fuzz
        return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))
    except Exception:
        import difflib
        r1 = difflib.SequenceMatcher(None, a, b).ratio()
        r2 = difflib.SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
        return 100.0 * max(r1, r2)

def fuzzy_command(low: str, app_names=(), max_words=6):
    """Best (phrase, score 0-100) among the local commands, or (None, 0). Short sentences only."""
    low = re.sub(r"[^a-z0-9' ]+", " ", (low or "").lower())
    low = re.sub(r"\s+", " ", low).strip()
    if not low or len(low.split()) > max_words:
        return None, 0.0
    phrases = list(FUZZY_PHRASES)
    for k in app_names or ():
        name = str(k).split("|")[0].strip().lower()
        if name:
            phrases += ["open " + name, "close " + name]
    cands = {low}
    m = _LEAD.match(low)
    if m and low[m.end():].strip():
        cands.add(low[m.end():].strip())
    best, score = None, 0.0
    for c in cands:
        for ph in phrases:
            # length guard: "close" vs "close jarvis" is not a near-miss, it's a different sentence
            if min(len(c), len(ph)) / max(len(c), len(ph)) < 0.6:
                continue
            s = _ratio(c, ph)
            if s > score:
                best, score = ph, s
    return best, float(score)

# Words that carry no command on their own. A transcript made only of these is noise or a stray word.
FILLER_WORDS = set("""
um umm ummm uh uhh uhhh uhm er erm ah ahh aah oh ooh eh huh hmm hm hmmm mm mmm mhm mm-hmm uh-huh hm-hm
ok okay k kay alright alr right so well like yeah yea ya yep yup nah no nope
made make this that these those one it is was the a an and but or then just
click clicked clicking tap close wait hold on hang sec second moment
what huh hey yo hi bye-bye bye
i i'm im you me my oh-no oops whoa wow lol bro dude man
""".split())
FILLER_PHRASES = {"this one", "that one", "this", "that", "made", "click", "um", "uh", "hmm", "ok", "okay", "close",
                  "bye bye", "oh", "yeah", "what", "huh", "wait", "hold on", "one sec", "hang on", "you know", "i mean",
                  "and", "so", "like", "right", "alright", "mm", "uh huh", "mm hmm", "oh no", "oops"}

def words_of(text: str):
    return re.findall(r"[a-z0-9']+(?:-[a-z0-9']+)?", (text or "").lower())

def is_filler(text: str) -> bool:
    low = normalize(text).lower().strip(" .!?,")
    if not low:
        return True
    if re.sub(r"[^a-z' ]", " ", low).strip() in FILLER_PHRASES:
        return True
    ws = words_of(low)
    return bool(ws) and all(w in FILLER_WORDS for w in ws)

def content_words(text: str) -> int:
    """Words that count toward the minimum (the wake word and 'please' don't)."""
    return len([w for w in words_of(normalize(text)) if w not in ("jarvis", "please", "hey")])

# kinds that are about JARVIS itself and always get through the gate
ALWAYS_KINDS = ("cancel", "jarvis_exit", "jarvis_sleep", "jarvis_wake", "dismiss")
# small talk a stray word can trigger; these need a pending question, or 2+ words, to count
STRAY_SMALL_TALK = ("affirm", "praise", "farewell", "are_you_there", "greet", "thanks")

def screen(text: str, it=None, rec=None, meta=None, followup=False, pending=False, app_names=()):
    """The gate in front of the brain. Returns (ok, reason).

    rec  = config["recognition"] (min_words, min_avg_logprob, max_no_speech_prob...)
    meta = what the recogniser reported: avg_logprob, no_speech_prob (missing for typed commands)
    """
    rec = rec or {}
    if it is None:
        it = parse(text, app_names)
    k = it.kind
    if k == "empty":
        return False, "empty"
    if k in ALWAYS_KINDS:
        return True, "control"
    meta = meta or {}
    lp, nsp = meta.get("avg_logprob"), meta.get("no_speech_prob")
    min_lp = float(rec.get("min_avg_logprob", -0.85))
    max_nsp = float(rec.get("max_no_speech_prob", 0.5))
    if followup:
        min_lp += float(rec.get("followup_stricter_by", 0.15))
    local = k not in ("forward", "clarify")
    if lp is not None and nsp is not None and nsp > max_nsp and lp < -0.4:
        return False, "no_speech"
    if lp is not None and lp < (min_lp - (0.35 if local else 0.0)):
        return False, "low_confidence"
    if pending and k in ("affirm", "cancel"):
        return True, "answer"
    if is_filler(text) and not (k == "affirm" and pending):
        return False, "filler"
    if k in STRAY_SMALL_TALK and content_words(text) < 2 and (followup or not pending):
        return False, "stray"
    if local:
        return True, "local"
    need = int(rec.get("followup_min_words" if followup else "min_words", rec.get("min_words", 3)))
    if content_words(text) < need:
        return False, "too_short"
    return True, "forward"

REASON_WORDS = {"filler": "filler", "too_short": "too short", "low_confidence": "unclear", "no_speech": "noise",
                "stray": "stray word", "empty": "nothing", "asleep": "asleep"}

def cancel_suffix(low: str) -> bool:
    """'open spotify, never mind' / 'that wasn't for you' at the end -> drop the whole thing."""
    return bool(re.search(r"(?:^|[\s,.;!?-])(?:never ?mind|nevermind|cancel(?: that| it)?|scratch that|forget it|disregard(?: that)?|"
                          r"that (?:was ?n'?t|wasn'?t|was not|isn'?t|is not) (?:for|meant for|to) you|not (?:talking )?to you|i'?m not talking to you|ignore that)$", low))
