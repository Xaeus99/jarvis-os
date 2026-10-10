"""Safety for image creation (shared with JARVIS for Windows v5.3). He's a minor, so this is strict and fails closed.
1) check_prompt(): blocks sexual/nude, gore and hate requests (with leetspeak / spacing tricks undone) and pictures
   of named real people. Refusals are short and in character.
2) NEGATIVE / SAFE_SUFFIX: added to every image prompt.
3) NsfwCheck: an image classifier (ViT, ONNX, runs on this PC) on every finished image. Anything scored as NSFW is
   deleted before it is shown or saved. If the checker can't load, no image is made at all.
Code creation has its own small refusal list (malware, password stealers...)."""
from __future__ import annotations
import codecs, logging, os, re

log = logging.getLogger("jarvisd.safety")

LEET = str.maketrans({"0": "o", "1": "i", "!": "i", "3": "e", "4": "a", "@": "a", "5": "s", "$": "s", "7": "t", "+": "t", "8": "b"})

# the adult-content word list is stored rot13-encoded so this public source file reads clean; decoded at import
ADULT = codecs.decode(r""""ahqr|ahqrf|ahqrm|ahqvgl|ahqvfg|anxrq|anxrl|abbq|abbqf|afsj|cbea\j*|c0ea|kkk|frk|frkl|frkhny\j*|frkv\j*|uragnv|rppuv|rebgvp\j*|yrjq\j*|
gbcyrff|obggbzyrff|fuvegyrff tvey|yvatrevr|haqrejrne|haqvrf|oen|oenf|cnagl|cnagvrf|ovxvav\j*|gubat\j*|fjvzfhvg|onguvat fhvg|
obbo\j*|oernfg\j*|gvgf?|gvggvrf|avccyrf?|pyrnintr|ohgg|ohggf|obbgl|nff|nffrf|guvpp|travgny\j*|cravf\j*|intvan\j*|qvpx|pbpx|chffl|
phz|betnfz\j*|ubeal|fgevc|fgevccre\j*|fgevccvat|fgevcgrnfr|baylsnaf|srgvfu\j*|oqfz|obaqntr|xvax\j*|frqhpgvir|frqhp\j*|frafhny|
cebibpngvir|haqerff\j*|jvgubhg pybgurf|ab pybgurf|pybgurf bss|hapybgurq|fxvzcl|va gur fubjre|va gur ongu|va orq jvgu|zvys|
e34|ehyr 34|ehyr34|tbba\j*|rqtvat|ovzob|rfpbeg|ubbxre|cebfgvghg\j*|znxvat bhg|serapu xvff\j*|ubg tveyf?|ubg jbzra|ubg jbzna|
fcernq yrtf|ync ?qnapr|gjrex\j*|cva ?hc|cynlobl|pnztvey|srrg cvpf?|sbbg srgvfu|oner ?purfgrq jbzna|obql cnvag""", "rot13")
GORE = r"""gore|gory|blood|bloody|bleeding|bloodbath|decapitat\w*|behead\w*|dismember\w*|mutilat\w*|corpse\w*|dead bod(?:y|ies)|
cadaver\w*|guts|entrails|intestines?|organs|severed|disembowel\w*|tortur\w*|murder\w*|massacre|execution|executed|suicide|
self ?harm|hanging body|hanged|lynch\w*|shot in the head|headshot wound|brains? out|skull crushed|autopsy|carnage|slaughter\w*|
splatter|impaled|flayed|skinned alive|cannibal\w*|wound\w*|injur\w*|stabbed|stabbing|school shooting|shooting people|gunshot"""
HATE = r"""swastika|nazi\w*|kkk|ku klux|white power|hitler|isis flag|terroris\w*"""
ALLOW = r"""blood moon|blood orange|bloodhound|blood red sky|bra(?:zil|ve|in|nch)\w*|assassin'?s creed|class|bass|grass|glass|
brass|mass|pass|cock ?pit|cocktail|peacock|dickens|butte|sussex|essex|middlesex|stripe\w*"""

_RE_ADULT = re.compile(r"\b(?:" + re.sub(r"\s*\n\s*", "", ADULT) + r")\b")
_RE_GORE = re.compile(r"\b(?:" + re.sub(r"\s*\n\s*", "", GORE) + r")\b")
_RE_HATE = re.compile(r"\b(?:" + re.sub(r"\s*\n\s*", "", HATE) + r")\b")
_RE_ALLOW = re.compile(r"\b(?:" + re.sub(r"\s*\n\s*", "", ALLOW) + r")\b")
_JOINED = tuple(codecs.decode(w, "rot13") for w in ('ahqr', 'anxrq', 'cbea', 'uragnv', 'afsj', 'gbcyrff', 'yvatrevr', 'baylsnaf', 'obbof', 'gvggvrf', 'avccyr', 'travgny', 'cravf', 'intvan', 'tber', 'qrpncvgng', 'qvfzrzore', 'fjnfgvxn'))

# Named real people: public figures he's likely to name (politicians, celebrities, streamers, athletes, musicians).
FAMOUS = r"""donald trump|trump|joe biden|biden|kamala harris|barack obama|obama|elon musk|musk|mark zuckerberg|zuckerberg|
jeff bezos|bill gates|elon|taylor swift|ariana grande|billie eilish|selena gomez|kim kardashian|kardashian|kylie jenner|beyonce|
rihanna|drake|kanye|kanye west|travis scott|nicki minaj|cardi b|doja cat|sabrina carpenter|olivia rodrigo|dua lipa|
dwayne johnson|the rock as|tom holland|zendaya|robert downey(?: jr)?|chris evans|chris hemsworth|scarlett johansson|
margot robbie|sydney sweeney|jenna ortega|millie bobby brown|timothee chalamet|ryan reynolds|keanu reeves|will smith|
lebron(?: james)?|michael jordan|cristiano ronaldo|ronaldo|lionel messi|messi|neymar|mbappe|tom brady|patrick mahomes|
mrbeast|mr beast|jimmy donaldson|pewdiepie|ninja the streamer|tyler blevins|ishowspeed|kai cenat|xqc|pokimane|valkyrae|
technoblade|markiplier|jacksepticeye|logan paul|jake paul|ksi|andrew tate|adin ross|caseoh|duke dennis|
king charles|prince harry|pope francis|putin|vladimir putin|zelensky|kim jong un|xi jinping|narendra modi|
emma watson|daniel radcliffe|tom cruise|leonardo dicaprio|brad pitt|angelina jolie|johnny depp|jennifer lawrence|
snoop dogg|eminem|post malone|bad bunny|justin bieber|ed sheeran|harry styles|lil nas x|ice spice|megan thee stallion"""
_RE_FAMOUS = re.compile(r"\b(?:" + re.sub(r"\s*\n\s*", "", FAMOUS) + r")\b")
PHOTO = re.compile(r"\b(?:photo\w*|realistic|real life|irl|selfie|portrait|headshot|deep ?fake\w*|face swap\w*|camera|dslr|"
                   r"4k photo|picture of a real|looks real|lifelike|hyper ?real\w*)\b")
PEOPLE_I_KNOW = re.compile(r"\b(?:my|our|his|her) (?:friend|bestie|best friend|classmate|teacher|principal|coach|mom|mum|dad|"
                           r"mother|father|brother|sister|cousin|aunt|uncle|grandma|grandpa|girlfriend|boyfriend|crush|ex|neighbou?r|boss)s?\b")
FICTIONAL = re.compile(r"\b(?:tony stark|iron man|peter parker|spider ?man|bruce wayne|batman|clark kent|superman|master chief|"
                       r"steve rogers|captain america|thor|hulk|mario|luigi|link|zelda|sonic|kratos|geralt|lara croft|"
                       r"harry potter|darth vader|luke skywalker|goku|naruto|shrek|santa(?: claus)?)\b")

def normalize(text):
    t = (text or "").lower().translate(LEET)
    t = re.sub(r"[^a-z' ]+", " ", t)
    t = re.sub(r"(.)\1{2,}", r"\1\1", t)          # "nuuuude" -> "nuude"
    t = re.sub(r"\s+", " ", t).strip()
    # "s e x y" -> "sexy": join runs of single letters
    t = re.sub(r"\b(?:[a-z] ){2,}[a-z]\b", lambda m: m.group(0).replace(" ", ""), t)
    return t

def check_prompt(prompt):
    """-> (ok, kind) where kind is None or one of adult / gore / hate / real_person."""
    raw = prompt or ""
    t = normalize(raw)
    t2 = re.sub(r"(.)\1+", r"\1", t)               # "nuude" -> "nude"
    probe = _RE_ALLOW.sub(" ", t)
    probe2 = re.sub(r"(.)\1+", r"\1", probe)
    for kind, rx in (("adult", _RE_ADULT), ("gore", _RE_GORE), ("hate", _RE_HATE)):
        if rx.search(probe) or rx.search(probe2):
            return False, kind
    joined = re.sub(r"[^a-z]", "", t2)
    for w in _JOINED:
        if w in joined and not _RE_ALLOW.search(t):
            return False, "gore" if w in ("gore", "decapitat", "dismember") else ("hate" if w == "swastika" else "adult")
    fic = FICTIONAL.sub(" ", t)
    if _RE_FAMOUS.search(fic):
        return False, "real_person"
    if PHOTO.search(t):
        if PEOPLE_I_KNOW.search(t) or re.search(r"\bdeep ?fake|face ?swap", t):
            return False, "real_person"
        # "a photo of Jake Miller": two capitalised words that aren't a known fictional character
        rawfic = FICTIONAL.sub(" ", raw.lower())
        for m in re.finditer(r"\b([A-Z][a-z]+) ([A-Z][a-z]+)\b", raw):
            pair = m.group(0).lower()
            if pair in rawfic and not re.match(r"(?:jarvis|iron|the|a|an|new|los|san|las)\b", pair):
                return False, "real_person"
    return True, None

REFUSALS = {
    "adult": ["I don't make that sort of picture, {sir}. Anything else, gladly.",
              "Not that one, {sir}. Pick another subject and I'll get to work."],
    "gore": ["I'll pass on the gore, {sir}. A battle scene without it, perhaps?",
             "Too grisly for me, {sir}. Try something a little less bloody."],
    "hate": ["Not that one, {sir}."],
    "real_person": ["I don't make pictures of real people, {sir}. A made-up character, gladly.",
                    "Real people are off the table for pictures, {sir}. A character of your own invention, though?"],
    "unsafe_output": ["That one came out wrong, {sir}, so I deleted it. Try a different description.",
                      "I binned that result, {sir}; it didn't pass my safety check. Another idea?"],
}

NEGATIVE = ("nsfw, nude, naked, nudity, sexual, suggestive, erotic, cleavage, lingerie, underwear, bikini, swimsuit, "
            "revealing clothes, gore, blood, violence, injury, wound, corpse, death, disturbing, horror, "
            "photo of a real person, celebrity, watermark, text, deformed, ugly")
SAFE_SUFFIX = ", safe for work, family friendly, fully clothed"

CODE_BAD = re.compile(r"\b(?:keylog\w*|steal\w*|password (?:grabber|stealer|cracker)|token (?:grabber|logger|stealer)|"
                      r"grab\w* (?:passwords|tokens|cookies)|ransomware|malware|virus|trojan|worm|rat|botnet|ddos\w*|dos attack|"
                      r"crypto ?miner|spyware|rootkit|backdoor|phish\w*|bypass (?:anti ?cheat|vac|easy ?anti ?cheat)|"
                      r"aim ?bot|aimbot|wall ?hack|cheat engine|cheats? for|hack (?:into|someone|my friend|an account|wifi)|"
                      r"brute ?force|delete system32|fork bomb|zip bomb|swat\w*|dox\w*)\b")

def check_code_request(text):
    t = normalize(text)
    if CODE_BAD.search(t):
        return False
    return True


class NsfwCheck:
    """ViT NSFW classifier (labels: normal, nsfw) through onnxruntime + OpenCV, both already in the JARVIS OS image."""
    def __init__(self, model_path, threshold=0.2):
        self.path, self.threshold = model_path, float(threshold)
        self.sess = None

    def load(self):
        if self.sess is None:
            import onnxruntime as ort
            self.sess = ort.InferenceSession(self.path, providers=["CPUExecutionProvider"])
            self.inp = self.sess.get_inputs()[0].name
        return self.sess

    @staticmethod
    def _prep(img):
        import cv2, numpy as np
        x = cv2.resize(img, (224, 224), interpolation=cv2.INTER_LINEAR)[:, :, ::-1].astype("float32") / 255.0  # BGR->RGB
        x = (x - 0.5) / 0.5
        return np.transpose(x, (2, 0, 1))[None].astype("float32")

    def score(self, image_path):
        """-> highest NSFW probability over the whole image and its centre crop (0..1)."""
        import cv2, numpy as np
        self.load()
        data = np.fromfile(image_path, dtype=np.uint8)          # works with any path encoding
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("unreadable image")
        h, w = img.shape[:2]
        c = min(h, w); y0, x0 = (h - c) // 2, (w - c) // 2
        crops = [img, img[y0:y0 + c, x0:x0 + c], img[: h // 2 + h // 4, :], img[h // 4:, :]]
        best = 0.0
        for cimg in crops:
            out = self.sess.run(None, {self.inp: self._prep(cimg)})[0][0]
            e = np.exp(out - out.max()); p = e / e.sum()
            best = max(best, float(p[1]))
        return best

    def is_safe(self, image_path):
        """Fails closed: any error counts as unsafe."""
        try:
            s = self.score(image_path)
            log.info("nsfw score %.3f for %s", s, os.path.basename(image_path))
            return s < self.threshold, s
        except Exception as e:
            log.warning("safety check failed (%r): treating as unsafe", e)
            return False, 1.0
