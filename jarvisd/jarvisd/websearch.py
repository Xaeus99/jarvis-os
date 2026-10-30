"""JARVIS OS 1.0.0-rc7: "search the web for ..." answered out loud, with sources (the Linux side of Windows JARVIS 7.3's
search). Free, no key, no account:

1. DuckDuckGo's plain HTML results page, strict safe search (kp=1); ads are skipped.
2. The top three pages are read in parallel (8 s each, 600 KB cap; scripts, menus and footers stripped).
3. The offline brain (the Ollama model JARVIS already uses) writes two to four sentences from those pages ONLY, citing
   them as [1], [2] ... JARVIS speaks it without the brackets and names the sites ("That's from techpowerup.com").
   No offline brain yet, or a game running (the model would take the graphics card): JARVIS reads the top result's
   own summary instead and names its site.
4. A notification shows the answer with the numbered links; "open source two" / "open that source" opens one.

Only the search words go to DuckDuckGo and the result pages; nothing goes to Tab, nothing paid is used, and no model
weights ship in the image. "google ...", "look up ..." and "search for ..." still open the search in the browser;
if the search can't be reached, "search the web for ..." does that too.
"""
from __future__ import annotations
import html as _html, json, logging, os, re, threading, urllib.parse, urllib.request
from html.parser import HTMLParser

log = logging.getLogger("jarvisd.websearch")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
      "Accept-Language": "en-GB,en;q=0.8"}
DDG = "https://html.duckduckgo.com/html/"
STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "jarvis", "last_search.json")
SYSTEM = ("You are J.A.R.V.I.S. answering a teenager's web question from search results. Use ONLY the numbered sources. "
          "Write 2 to 4 short plain sentences, cite each fact like [1] or [2][3], and never invent a source number. "
          "If the sources don't answer it, say so in one sentence. No markdown, no lists, no URLs.")

_NUM = {"one": 1, "first": 1, "1": 1, "two": 2, "second": 2, "2": 2, "to": 2, "too": 2, "three": 3, "third": 3, "3": 3,
        "four": 4, "fourth": 4, "4": 4, "five": 5, "fifth": 5, "5": 5, "six": 6, "sixth": 6, "6": 6}


def _clean(text):
    t = (text or "").strip()
    t = re.sub(r"[!.,]+$", "", t)
    t = re.sub(r"^(?:(?:hey |ok |okay )?jarvis[, ]*)", "", t, flags=re.I)
    t = re.sub(r"^(?:please |can you |could you |would you |go ahead and )+", "", t, flags=re.I).strip()
    t = re.sub(r"(?:,? please|,? for me|,? jarvis)+$", "", t, flags=re.I).strip()
    return t


def parse_voice(text):
    """-> ("search", query) | ("open_source", n) | None.
    "search the web for X", "search online for X", "search the internet for X", "web search X",
    "what does the web say about X", "search the web and tell me X"; "open source two", "open that source"."""
    t = _clean(text)
    low = t.lower()
    m = re.fullmatch(r"(?:search|check|scan) (?:the )?(?:web|internet|net|online)(?: and tell me| for me)?(?: (?:for|about|on))? (.+)|"
                     r"search online (?:for |about )?(.+)|web ?search(?: for)? (.+)|"
                     r"what does the (?:web|internet) say (?:about|on) (.+)", low)
    if m:
        q = next(g for g in m.groups() if g)
        start = low.rfind(q)
        q = t[start:start + len(q)].strip(" ?")
        return ("search", q) if q else None
    m = re.fullmatch(r"open (?:the )?(?:(\w+) )?(?:source|link)(?: (?:number )?(\w+))?(?: from (?:the|that|your) search)?", low)
    if m:
        w = m.group(2) or m.group(1) or "1"
        if w in ("that", "the", "this", "top"):
            w = "1"
        n = _NUM.get(w)
        if n:
            return ("open_source", n)
    return None


def _get(url, timeout=8, max_bytes=600_000, opener=urllib.request.urlopen, data=None):
    req = urllib.request.Request(url, headers=UA, data=data)
    with opener(req, timeout=timeout) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        raw = r.read(max_bytes)
    enc = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        enc = m.group(1)
    return raw.decode(enc, "replace"), ctype


def _unwrap(href):
    """DuckDuckGo's //duckduckgo.com/l/?uddg=<real url> -> the real url."""
    h = _html.unescape(href or "")
    if h.startswith("//"):
        h = "https:" + h
    q = urllib.parse.urlparse(h)
    if "duckduckgo.com" in q.netloc and q.path.startswith("/l/"):
        u = urllib.parse.parse_qs(q.query).get("uddg")
        if u:
            return u[0]
    return h


def parse_results(page, n=6):
    """-> [{"title", "url", "snippet"}] from DuckDuckGo's HTML page; sponsored results are skipped."""
    out, seen = [], set()
    for b in re.split(r'<div[^>]+class="[^"]*?\bresult\b', page or "")[1:]:
        if "result--ad" in b[:300] or "badge--ad" in b[:2000] or "y.js?ad_" in b:
            continue
        m = re.search(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.S) or \
            re.search(r'<a[^>]+href="([^"]+)"[^>]+class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        url = _unwrap(m.group(1))
        if not url.startswith("http") or url in seen:
            continue
        sm = re.search(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div|td)>', b, re.S)
        title = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))).strip()
        snip = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", sm.group(1)))).strip() if sm else ""
        seen.add(url)
        out.append({"title": title, "url": url, "snippet": snip})
        if len(out) >= n:
            break
    return out


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "svg", "form", "aside", "iframe", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.parts = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag in ("p", "br", "li", "h1", "h2", "h3", "tr", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def page_text(page, limit=2000):
    p = _Text()
    try:
        p.feed(page or "")
    except Exception:
        pass
    txt = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    lines = [ln.strip() for ln in txt.split("\n")]
    lines = [ln for ln in lines if len(ln) > 40]            # menus and buttons are short; sentences aren't
    return re.sub(r"\s+", " ", " ".join(lines))[:limit]


def site_name(url):
    """Spoken source name: www.techpowerup.com -> techpowerup.com, en.m.wikipedia.org -> wikipedia.org."""
    h = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    parts = h.split(".")
    while len(parts) > 2 and (parts[0] in ("www", "m", "mobile", "amp") or re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", parts[0])):
        parts = parts[1:]
    return ".".join(parts)


def strip_citations(text):
    t = re.sub(r"\s*\[(?:\d+(?:\s*[,-]\s*\d+)*)\](?:\[\d+\])*", "", text or "")
    return re.sub(r"\s+([.,;:!?])", r"\1", t).strip()


def first_sentences(text, n=2, limit=320):
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "").strip())
    out = " ".join(parts[:n]).strip()
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return out


_STOP = {"the", "a", "an", "of", "is", "are", "was", "what", "who", "how", "much", "many", "does", "do", "did", "in", "on",
         "for", "to", "and", "or", "my", "me", "it", "its", "this", "that", "when", "where", "which", "with", "have", "has"}


def best_snippet(query, sources):
    """The result whose summary shares the most words with the question (ties: the higher-ranked one)."""
    q = {w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if w not in _STOP}
    best, score = None, -1
    for s in sources:
        words = set(re.findall(r"[a-z0-9]+", ((s.get("text") or "") + " " + (s.get("title") or "")).lower()))
        sc = len(q & words) if s.get("text") else -1
        if sc > score:
            best, score = s, sc
    return best


def save_last(d, path=None):
    path = path or STATE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, path)
    except OSError as e:
        log.info("search: could not save the last search: %r", e)


def load_last(path=None):
    try:
        return json.load(open(path or STATE))
    except (OSError, ValueError):
        return {}


class WebSearch:
    """answer(query) -> {"ok", "spoken", "text", "sources", "how"}; how = "brain" | "snippet" | reason."""
    def __init__(self, ai=None, in_game=lambda: False, opener=urllib.request.urlopen, max_pages=3, state=None):
        self.ai, self.in_game, self.opener, self.max_pages, self.state = ai, in_game, opener, max_pages, state

    def search(self, query, n=6):
        data = urllib.parse.urlencode({"q": query, "kp": "1", "kl": "uk-en"}).encode()
        page, _ = _get(DDG, timeout=10, opener=self.opener, data=data)
        return parse_results(page, n)

    def read_pages(self, results):
        out = [None] * len(results)

        def one(i, r):
            try:
                page, ctype = _get(r["url"], timeout=8, opener=self.opener)
                if "html" in ctype or page.lstrip().startswith("<"):
                    out[i] = page_text(page)
            except Exception as e:
                log.info("search: page %s: %r", r["url"], e)
        ts = [threading.Thread(target=one, args=(i, r), daemon=True) for i, r in enumerate(results)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(12)
        return out

    def brain_ready(self):
        try:
            return bool(self.ai is not None and self.ai.ready())
        except Exception:
            return False

    def ask_brain(self, query, sources, timeout=150):
        from . import localai
        ctx = "\n\n".join(f"[{i + 1}] {s['title']} ({site_name(s['url'])})\n{s['text']}" for i, s in enumerate(sources))
        body = {"model": self.ai.model(), "stream": False, "keep_alive": "10m",
                "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 220},
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": f"Sources:\n{ctx}\n\nQuestion: {query}"}]}
        with localai._req(self.ai.c["url"] + "/api/chat", body, timeout=timeout, opener=self.ai.opener) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
        txt = (d.get("message") or {}).get("content", "")
        return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()

    def answer(self, query, sir="sir"):
        from . import safety
        ok, kind = safety.check_prompt(query)
        if not ok and kind in ("adult", "gore", "hate"):
            return {"ok": False, "how": "refused", "spoken": f"I won't search for that one, {sir}.", "sources": []}
        try:
            results = self.search(query)
        except Exception as e:
            log.info("search: DuckDuckGo unreachable: %r", e)
            return {"ok": False, "how": "offline", "spoken": "", "sources": []}
        if not results:
            return {"ok": False, "how": "empty", "spoken": f"The search came back empty, {sir}. Try other words?", "sources": []}
        top = results[: self.max_pages]
        gaming = False
        try:
            gaming = bool(self.in_game())
        except Exception:
            pass
        use_brain = not gaming and self.brain_ready()
        txt, how = "", "snippet"
        if use_brain:
            texts = self.read_pages(top)
            sources = [dict(r, text=(t or r.get("snippet") or "")[:2000]) for r, t in zip(top, texts)]
            sources += [dict(r, text=r["snippet"]) for r in results[self.max_pages:] if r.get("snippet")]
            sources = [s for s in sources if s["text"]][:6]
            try:
                txt = self.ask_brain(query, sources)
                how = "brain"
            except Exception as e:
                log.info("search: offline brain failed: %r", e)
                txt = ""
        else:
            sources = [dict(r, text=r.get("snippet") or "") for r in results]
        if txt:
            used = sorted({int(n) for n in re.findall(r"\[(\d+)\]", txt) if 0 < int(n) <= len(sources)})
            names = list(dict.fromkeys(site_name(sources[i - 1]["url"]) for i in used))[:2]
            spoken = strip_citations(txt)
            spoken = re.sub(r"https?://\S+", "", spoken).strip()
            if names:
                spoken += f" That's from {' and '.join(names)}."
        else:
            how = "snippet"
            best = best_snippet(query, sources[: self.max_pages]) or sources[0]
            used = [sources.index(best) + 1]
            gist = first_sentences(best.get("text") or best["title"])
            spoken = f"The top result, from {site_name(best['url'])}, says: {gist}"
            if gaming:
                spoken += f" I'll keep the full write-up for after your game, {sir}."
        body = (strip_citations(txt) if txt else spoken) + "\n\nSources:\n" + \
            "\n".join(f"[{i + 1}] {s['title']}\n    {s['url']}" for i, s in enumerate(sources[:6]))
        save_last({"query": query, "text": txt or spoken, "sources": [{"title": s["title"], "url": s["url"]} for s in sources[:6]],
                   "how": how}, self.state)
        return {"ok": True, "how": how, "spoken": spoken, "text": body, "sources": sources, "cited": used}


def search_url(query):
    """The browser fallback: the same search the old "search the web for" opened."""
    return "https://duckduckgo.com/?" + urllib.parse.urlencode({"q": query, "kp": "1"})
