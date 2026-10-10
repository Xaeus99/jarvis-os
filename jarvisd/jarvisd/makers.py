"""Voice commands for making things (shared design with JARVIS for Windows v5.3): code and images, local and $0.

parse(text) -> (action, extra) or None;  Makers(brain).handle(action, extra) runs it.
  "write a python script that …" / "code me a …"   -> CodeMaker (never runs the code; big builds go to Tab when linked)
  "make an image of …" / "draw …" / "another one"    -> ImageMaker (safety first; refused while a game runs)
  "open my images" / "open my code"                  -> the folders
  "set up the image engine" / "can you make images"  -> downloads / status
"""
from __future__ import annotations
import logging, os, random, re, subprocess, threading

from . import creator, safety

log = logging.getLogger("jarvisd.makers")

LANGS = r"(?:python|java ?script|js|type ?script|html|css|batch|power ?shell|c\+\+|c ?sharp|c#|java|lua|luau|rust|go|sql|bash|gd ?script|roblox|unity)"
CODE_RX = re.compile(
    r"(?:(?:can you |could you |please )*)"
    r"(?:(?:write|make|create|code|build|generate|give)(?: me)?(?: up)? (?:a |an |some |the )?(?:quick |simple |small |little )?"
    r"(?:[\w+#.-]+ ){0,3}?(?:code|script|program|function|snippet|class|macro|tool|calculator)\b(.*)"
    r"|code (?:me )?(?:a |an |up )(.+)|write (?:a |an |some )?" + LANGS + r" (.+))", re.I)
IMG_RX = re.compile(
    r"(?:(?:can you |could you |please )*)"
    r"(?:(?:make|create|generate|draw|paint|render|design)(?: me| us)? (?:an? |some |the )?(?:ai |cool |quick )?"
    r"(?:image|picture|pic|drawing|painting|art|artwork|illustration|wallpaper|logo|icon|poster|concept art|sketch)s?"
    r"(?: (?:of|showing|with|for|that shows|where))? (.+)"
    r"|draw (?:me |us )?(?:an? |some )?(.+)|imagine (?:an? )?(.+)|paint (?:me )?(?:an? )?(.+))", re.I)
NOT_CODE = re.compile(r"\b(?:qr code|discount code|promo code|zip code|area code|code word|source code of|cheat code|link code)\b")

def _strip(text):
    return re.sub(r"^(?:hey )?jarvis,? ", "", (text or "").strip(" .!?,"), flags=re.I)

def parse(text):
    raw = _strip(text)
    t = raw.lower()
    if not t:
        return None
    F = re.fullmatch
    if F(r"(?:make|do|generate|draw|paint) (?:another|a new|a different) (?:one|image|picture|version)(?: of (?:that|it))?|"
         r"(?:another|a new|a different) (?:image|picture|version)(?: of (?:that|it))?|"
         r"regenerate(?: (?:it|that|the image|the picture))?|try (?:that|the image|the picture) again|re ?roll(?: (?:it|that))?", t):
        return "image_again", {}
    if F(r"(?:open|show)(?: me)? (?:my |the )?(?:jarvis )?(?:images?|pictures?)(?: folder)?|where are my (?:jarvis )?images", t):
        return "image_folder", {}
    if F(r"(?:open|show)(?: me)? (?:my |the )?(?:jarvis )?code(?: folder)?|where(?:'s| is) my code", t):
        return "code_folder", {}
    if F(r"(?:set ?up|download|install|get) (?:the |your )?(?:image|picture|art) (?:engine|maker|generator|creation|model)|"
         r"(?:set ?up|download|install|get) (?:the |your )?(?:coding|code) (?:brain|model|ai)|(?:image|picture) (?:engine|maker) status|"
         r"can you make (?:images|pictures|code)", t):
        return "maker_status", {"setup": bool(re.match(r"(?:set ?up|download|install|get)", t))}
    if F(r"(?:make|create|generate|draw|paint)(?: me)? (?:an? )?(?:image|picture|pic|drawing|painting)|draw something", t):
        return "image_what", {}
    m = IMG_RX.fullmatch(raw)
    if m:
        prompt = next((g for g in m.groups() if g), "").strip(" .,!?")
        if prompt and not re.fullmatch(r"(?:it|that|this|something|anything|one|a picture|an image)", prompt.lower()):
            return "image", {"prompt": prompt}
        return "image_what", {}
    if CODE_RX.fullmatch(t) and not NOT_CODE.search(t):
        return "code", {"request": raw}
    return None


class Makers:
    def __init__(self, brain, code=None, image=None, start=None):
        self.b = brain
        self._code, self._image = code, image
        self.start = start or (lambda fn: threading.Thread(target=fn, daemon=True, name="maker").start())
        self.busy = threading.Lock()

    @property
    def code(self):
        if self._code is None:
            self._code = creator.CodeMaker(self.b.cfg)
        return self._code

    @property
    def image(self):
        if self._image is None:
            self._image = creator.ImageMaker(self.b.cfg)
        return self._image

    def handle(self, action, x):
        b, s = self.b, self.b.sir
        if action == "image_folder":
            d = self.image.out_dir; os.makedirs(d, exist_ok=True); b.launch(["xdg-open", d])
            return b.say(f"Your JARVIS images, {s}.")
        if action == "code_folder":
            d = self.code.out_dir; os.makedirs(d, exist_ok=True); b.launch(["xdg-open", d])
            return b.say(f"Your JARVIS code, {s}.")
        if action == "image_what":
            return b.say(f"What shall I make a picture of, {s}?")
        if action == "maker_status":
            return self.status(x.get("setup"))
        if action in ("image", "image_again"):
            return self.make_image(x.get("prompt", ""), again=action == "image_again")
        if action == "code":
            return self.make_code(x.get("request", ""))
        return b.say(f"Not sure what to do with that, {s}.")

    # ---- status / setup ----
    def status(self, setup):
        b, s = self.b, self.b.sir
        img, code = self.image, self.code
        try:
            have_code = code.have_model()
        except Exception:
            have_code = False
        parts = ["Pictures: ready." if img.installed() else f"Pictures: not downloaded yet, about {creator.IMAGE_GB} gigabytes on first use.",
                 "Code: ready." if have_code else f"Code: my coding brain isn't downloaded yet, about {creator.CODER_GB} gigabytes."]
        if setup:
            def go():
                try:
                    if not img.installed():
                        img.install(); b.say(f"My image engine is ready, {s}.")
                    if not code.have_model() and code.ensure_model():
                        b.say(f"My coding brain is ready, {s}.")
                except Exception as e:
                    log.exception("maker setup"); b.say(f"The download stopped, {s}: {str(e)[:80]}. Ask again and I'll resume it.")
            msg = b.say(f"Downloading now, {s}. I'll tell you when it's done. " + " ".join(parts))
            self.start(go)
            return msg
        if all(p.endswith("ready.") for p in parts):
            return b.say(" ".join(parts))
        return b.say(" ".join(parts) + f" Say \u201cset up the image engine\u201d to download it now, {s}.")

    # ---- images ----
    def make_image(self, prompt, again=False):
        b, s = self.b, self.b.sir
        img = self.image
        if again:
            if not img.last:
                return b.say(f"There's nothing to redo yet, {s}. Tell me what to draw.")
            prompt = img.last["prompt"]
        ok, kind = safety.check_prompt(prompt)
        if not ok:
            return b.say(random.choice(safety.REFUSALS[kind]).format(sir=s))
        if b.is_gaming():
            return b.say(f"After your match, {s}. Making pictures needs the graphics card your game is using.")
        if img.lock.locked():
            return b.say(f"Still painting the last one, {s}. One at a time.")
        first = not img.installed()
        def go():
            with img.lock:
                try:
                    if first:
                        img.install()
                    res, detail = img.generate(prompt)
                    if res == "ok":
                        b.say(random.choice([f"Here it is, {s}.", f"Done, {s}. It's on screen and saved in your JARVIS Images.",
                                             f"Fresh off the canvas, {s}."]))
                    elif res == "unsafe":
                        b.say(random.choice(safety.REFUSALS["unsafe_output"]).format(sir=s))
                    elif res == "refused":
                        b.say(random.choice(safety.REFUSALS[detail]).format(sir=s))
                    else:
                        b.say(f"The image engine stumbled, {s}. Try again in a moment.")
                except Exception as e:
                    log.exception("image")
                    b.say(f"I couldn't set up the image engine, {s}: {str(e)[:90]}. Ask again and I'll resume the download.")
        if first:
            msg = (f"First picture, {s}: I'm downloading my image engine, about {creator.IMAGE_GB} gigabytes, once. "
                   f"I'll paint it the moment it's in.")
        elif img.build == "cpu":
            msg = f"Painting it now, {s}. Without a graphics card that takes a minute or two."
        else:
            msg = random.choice([f"Painting it now, {s}.", f"On it, {s}. A few seconds.", f"Picture coming up, {s}."])
        b.say(msg)                      # the acknowledgement first, then the work
        self.start(go)
        return msg

    # ---- code ----
    def make_code(self, req):
        b, s = self.b, self.b.sir
        if not safety.check_code_request(req):
            return b.say(f"I won't write that one, {s}. Something else, gladly.")
        if creator.is_big_build(req) and b.forward:
            b.forward(req)
            return b.say(f"That's a bigger build, {s}. Passing it to Tab.")
        if not b.ai.enabled:
            return b.say(f"My offline brain is switched off in my settings, {s}, so I can't write code here.")
        code = self.code
        if code.lock.locked():
            return b.say(f"Still writing the last one, {s}.")
        try:
            need_pull = not code.have_model()
        except Exception:
            need_pull = True
        def go():
            with code.lock:
                try:
                    if need_pull and not code.ensure_model():
                        b.say(f"My coding brain didn't download, {s}. Check the internet and ask again.")
                        return
                    path, src, lang, note = code.write(req)
                    n = len(src.splitlines())
                    spoken = {"cpp": "C plus plus", "csharp": "C sharp", "js": "JavaScript", "javascript": "JavaScript"}.get(lang, lang)
                    b.say(f"Done, {s}: {n} lines of {spoken}. It's on screen and saved in JARVIS Code. I haven't run it.")
                except Exception:
                    log.exception("code")
                    b.say(f"My coding brain tripped on that one, {s}. Try asking a little differently.")
        if need_pull:
            msg = (f"First time writing code, {s}: I'm downloading my coding brain, about {creator.CODER_GB} gigabytes, once. "
                   f"Then I'll write it.")
        else:
            msg = random.choice([f"Writing it now, {s}.", f"On it, {s}. Give me a moment.", f"Coding, {s}."])
        b.say(msg)
        self.start(go)
        return msg
