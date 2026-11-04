r"""rc10: data charts by voice on JARVIS OS, a port of Windows JARVIS 7.9.3's charts.

"chart US unemployment since 2000", "graph the price of milk since 1920", "compare eggs and bread", "chart world population",
then "adjust for inflation", "make it a bar chart", "save this chart" (a PNG in ~/Pictures/JARVIS Charts).
The chart opens in its own small window (python -m jarvisd.chartwin) and JARVIS says what it shows in one sentence.

Rules: real numbers only. Every point comes from a named free public source (no keys, no accounts):
  * the offline set in jarvisd/assets/charts/curated.json (BLS average prices + CPI-U, DOL minimum wage) - always works
  * BLS public API v1 (no key; 25 requests a day per PC) for more average-price series
  * World Bank API (population, GDP, life expectancy ... for ~60 countries)
  * Our World in Data grapher CSV (CO2, life expectancy, ...)
  * FRED fredgraph.csv (mortgage rates, unemployment, GDP, interest rates ...)
If nothing solid is found, JARVIS says so and draws nothing. Missing years stay gaps; nothing is filled in.
Inflation: real = nominal x CPI-U(latest full year) / CPI-U(year), from the offline CPI-U table (1913 on).
Rendering is plain QPainter (no matplotlib); render() returns a QImage, save_png() writes it."""
from __future__ import annotations
import csv, io, json, logging, math, os, re, threading, urllib.parse, urllib.request

log = logging.getLogger("jarvisd.charts")
HERE = os.path.dirname(os.path.abspath(__file__))
CURATED = os.path.join(HERE, "assets", "charts", "curated.json")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) desktop-assistant charts", "Accept": "*/*"}
_CACHE = {"curated": None}


class NoData(Exception):
    """Raised with a sentence JARVIS can say when there is no solid data for the ask."""


# ------------------------------------------------------------------ catalog: words -> where the real numbers live
# kind curated: the offline file; bls: BLS v1 series id; wb: World Bank indicator (per country); owid: grapher slug + column;
# fred: FRED series id (fredgraph.csv).
CATALOG = [
    # key, regex, refs, title-override
    ("milk", r"\bmilk\b", [("curated", "milk_gallon"), ("curated", "milk_half_gallon")]),
    ("dairy", r"\bdairy\b", [("curated", "dairy_index")]),
    ("bread", r"\bbread\b|\bloaf\b", [("curated", "bread")]),
    ("eggs", r"\beggs?\b", [("curated", "eggs")]),
    ("gas", r"\bgas(?:oline)?\b|\bpetrol\b|\bfuel\b", [("curated", "gas")]),
    ("beef", r"\bground beef\b|\bbeef\b|\bhamburger meat\b", [("curated", "beef")]),
    ("min_wage", r"\bminimum wage\b|\bmin wage\b", [("curated", "min_wage")]),
    ("cpi", r"\bcpi\b|\bconsumer price index\b|\bcost of living\b|\binflation\b|\bprice level\b", [("curated", "cpi")]),
    ("coffee", r"\bcoffee\b", [("bls", "APU0000717311", "Coffee, 100% ground roast, per lb", "$ per lb")]),
    ("bananas", r"\bbananas?\b", [("bls", "APU0000711211", "Bananas, per lb", "$ per lb")]),
    ("chicken", r"\bchicken\b", [("bls", "APU0706111", "Chicken, fresh whole, per lb", "$ per lb")]),
    ("electricity", r"\belectricity\b|\bpower bill\b|\belectric (?:price|bill|rate)", [("bls", "APU000072610", "Electricity, per kWh", "$ per kWh")]),
    ("unemployment", r"\bunemployment\b|\bjobless\b", [("bls", "LNS14000000", "U.S. unemployment rate", "% of labor force"),
                                                         ("wb", "SL.UEM.TOTL.ZS", "Unemployment rate (ILO estimate, World Bank)", "% of labor force", "fallback")]),
    ("mortgage", r"\bmortgage(?: rates?)?\b", [("fred", "MORTGAGE30US", "30-year fixed mortgage rate", "%")]),
    ("fed_rate", r"\binterest rates?\b|\bfed(?:eral)? funds\b|\bfed rate\b", [("fred", "FEDFUNDS", "Federal funds rate", "%")]),
    ("debt", r"\bnational debt\b|\bfederal debt\b|\bus debt\b", [("fred", "GFDEBTN", "U.S. federal debt", "$ millions")]),
    ("house_price", r"\bhouse prices?\b|\bhome prices?\b|\bhousing prices?\b", [("fred", "MSPUS", "Median U.S. home sale price", "$")]),
    ("population", r"\bpopulation\b|\bhow many people\b", [("wb", "SP.POP.TOTL", "Population", "people")]),
    ("gdp_pc", r"\bgdp per (?:capita|person)\b|\bincome per person\b", [("wb", "NY.GDP.PCAP.CD", "GDP per person (current US$)", "US$")]),
    ("gdp", r"\bgdp\b|\beconomy size\b|\bgross domestic product\b", [("wb", "NY.GDP.MKTP.CD", "GDP (current US$)", "US$")]),
    ("life_exp", r"\blife expectancy\b|\bhow long people live\b", [("owid", "life-expectancy", "life_expectancy_0", "Life expectancy at birth", "years")]),
    ("co2", r"\bco2\b|\bcarbon (?:dioxide )?emissions?\b|\bemissions\b", [("owid", "co-emissions-per-capita", "emissions_total_per_capita", "CO\u2082 emissions per person", "tonnes")]),
    ("internet", r"\binternet (?:users|use|usage)\b|\bpeople online\b", [("wb", "IT.NET.USER.ZS", "Internet users", "% of population")]),
    ("fertility", r"\bfertility\b|\bbirth rate\b|\bbabies per woman\b", [("wb", "SP.DYN.TFRT.IN", "Births per woman", "births")]),
]
COUNTRIES = {"united states": "USA", "us": "USA", "usa": "USA", "u s": "USA", "america": "USA", "the us": "USA", "uk": "GBR",
             "united kingdom": "GBR", "britain": "GBR", "england": "GBR", "canada": "CAN", "mexico": "MEX", "brazil": "BRA",
             "argentina": "ARG", "france": "FRA", "germany": "DEU", "italy": "ITA", "spain": "ESP", "portugal": "PRT",
             "ireland": "IRL", "netherlands": "NLD", "belgium": "BEL", "sweden": "SWE", "norway": "NOR", "denmark": "DNK",
             "finland": "FIN", "poland": "POL", "ukraine": "UKR", "russia": "RUS", "turkey": "TUR", "greece": "GRC",
             "switzerland": "CHE", "austria": "AUT", "china": "CHN", "india": "IND", "japan": "JPN", "south korea": "KOR",
             "korea": "KOR", "north korea": "PRK", "indonesia": "IDN", "pakistan": "PAK", "bangladesh": "BGD", "vietnam": "VNM",
             "philippines": "PHL", "thailand": "THA", "australia": "AUS", "new zealand": "NZL", "nigeria": "NGA", "egypt": "EGY",
             "south africa": "ZAF", "kenya": "KEN", "ethiopia": "ETH", "saudi arabia": "SAU", "iran": "IRN", "israel": "ISR",
             "chile": "CHL", "colombia": "COL", "peru": "PER", "cuba": "CUB", "jamaica": "JAM", "iceland": "ISL",
             "the world": "WLD", "world": "WLD", "the whole world": "WLD", "earth": "WLD"}
COUNTRY_NAMES = {"USA": "United States", "GBR": "United Kingdom", "WLD": "World"}
WORDS_NUM = {"nineteen twenty": 1920, "nineteen hundred": 1900}


def _norm(q):
    q = (q or "").lower().replace("\u2019", "'")
    q = re.sub(r"[^\w$%' .-]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def years_of(q):
    """'since 1920' / 'from 1950 to 2000' / 'over the last 30 years' / 'since the 1970s' -> (start, end) or (None, None)."""
    q = _norm(q)
    for w, n in WORDS_NUM.items():
        q = q.replace(w, str(n))
    import datetime
    now = datetime.date.today().year
    m = re.search(r"\b(?:from|between) (1[6-9]\d\d|20\d\d)s? (?:to|and|through|until|-) (1[6-9]\d\d|20\d\d|now|today)\b", q)
    if m:
        b = now if m.group(2) in ("now", "today") else int(m.group(2))
        return int(m.group(1)), b
    m = re.search(r"\b(?:since|from|after|starting(?: in)?|beginning(?: in)?) (?:the )?(1[6-9]\d\d|20\d\d)s?\b", q)
    if m:
        return int(m.group(1)), None
    m = re.search(r"\b(?:last|past) (\d{1,3}) years\b", q)
    if m:
        return now - int(m.group(1)), None
    m = re.search(r"\b(?:until|to|through|before) (1[6-9]\d\d|20\d\d)\b", q)
    if m:
        return None, int(m.group(1))
    m = re.search(r"\bin the (1[6-9]\d0|20\d0)s\b", q)
    if m:
        return int(m.group(1)), int(m.group(1)) + 9
    return None, None


def country_of(q):
    q = " " + _norm(q) + " "
    best = None
    for name, code in COUNTRIES.items():
        if re.search(r"[\s,](?:in |of |for )?" + re.escape(name) + r"(?:'s)?[\s,]", q):
            if best is None or len(name) > len(best[0]):
                best = (name, code)
    return best[1] if best else None


def topic_of(q):
    """The CATALOG entry the words ask for, or None."""
    q = _norm(q)
    for key, rx, refs in CATALOG:
        if re.search(rx, q):
            return key, refs
    return None


LLM = {"ask": None}          # optional: ask(prompt) -> text, the local model on his PC (never a source of numbers)


def llm_topic(q, ask=None):
    """When no keyword matches, the local model may only PICK one catalog key (or none). It never supplies data."""
    ask = ask or LLM.get("ask")
    if not ask:
        return None
    keys = [k for k, _, _ in CATALOG]
    prompt = ("Pick the ONE data series that answers this chart request, from this list only: " + ", ".join(keys) +
              ". Answer with just the key, or none if nothing fits.\nRequest: " + q)
    try:
        ans = (ask(prompt) or "").strip().lower()
    except Exception:
        return None
    ans = re.sub(r"<think>.*?</think>", "", ans, flags=re.S).strip().strip(".'\"` ")
    for k, rx, refs in CATALOG:
        if ans == k:
            return k, refs
    return None


def split_compare(q):
    """'compare eggs and bread' / 'milk vs eggs' / 'the population of france and germany' -> list of parts (2-3)."""
    q = _norm(q)
    q = re.sub(r"^(?:compare|comparing|a comparison of|plot|graph|chart) (?:the )?", "", q)
    parts = re.split(r"\s+(?:and|vs\.?|versus|with|against|to)\s+(?:the )?", q)
    return [p.strip() for p in parts if p.strip()][:3]


# ------------------------------------------------------------------ data
def curated(path=None):
    if path is None and _CACHE["curated"] is not None:
        return _CACHE["curated"]
    with open(path or CURATED, encoding="utf-8") as f:
        doc = json.load(f)
    out = {}
    for k, s in doc["series"].items():
        vals = {int(y): float(v) for y, v in s["values"].items() if v is not None}
        out[k] = dict(s, key=k, values=vals)
    if path is None:
        _CACHE["curated"] = out
    return out


def _get(url, opener=None, data=None, timeout=12, headers=None):
    req = urllib.request.Request(url, headers=dict(UA, **(headers or {})), data=data)
    with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
        return r.read(4_000_000)


def fetch_bls(sid, title, unit, start=None, end=None, opener=None):
    import datetime
    end = end or datetime.date.today().year
    start = max(start or end - 29, 1913)
    vals, mon = {}, {}
    y = start
    while y <= end:                                 # v1: 10 years per request, no key
        body = json.dumps({"seriesid": [sid], "startyear": str(y), "endyear": str(min(y + 9, end))}).encode()
        doc = json.loads(_get("https://api.bls.gov/publicAPI/v1/timeseries/data/", opener, data=body,
                              headers={"Content-Type": "application/json"}))
        if doc.get("status") != "REQUEST_SUCCEEDED":
            raise NoData("the labour statistics service said no (" + "; ".join(doc.get("message") or [])[:80] + ")")
        for s in doc["Results"]["series"]:
            for d in s.get("data") or []:
                if str(d.get("period", "")).startswith("M") and d["period"] != "M13":
                    try:
                        mon.setdefault(int(d["year"]), []).append(float(d["value"]))
                    except (ValueError, TypeError):
                        pass
        y += 10
    for yr, vs in mon.items():
        if len(vs) >= 6:
            vals[yr] = round(sum(vs) / len(vs), 4)
    return {"title": title, "unit": unit, "values": vals, "url": "https://data.bls.gov/timeseries/" + sid,
            "source": "U.S. Bureau of Labor Statistics (series %s), annual average" % sid}


def fetch_worldbank(ind, title, unit, country="USA", opener=None):
    url = "https://api.worldbank.org/v2/country/%s/indicator/%s?format=json&per_page=400" % (country, ind)
    doc = json.loads(_get(url, opener))
    if not isinstance(doc, list) or len(doc) < 2 or not doc[1]:
        raise NoData("the World Bank has no figures for that")
    vals = {}
    name = COUNTRY_NAMES.get(country, country)
    for row in doc[1]:
        if row.get("value") is not None:
            vals[int(row["date"])] = float(row["value"])
        name = (row.get("country") or {}).get("value") or name
    return {"title": "%s, %s" % (title, name), "unit": unit, "values": vals, "url": "https://data.worldbank.org/indicator/" + ind,
            "source": "World Bank, World Development Indicators (%s)" % ind}


def fetch_owid(slug, col, title, unit, country="USA", opener=None):
    url = "https://ourworldindata.org/grapher/%s.csv?v=1&csvType=filtered&useColumnShortNames=true&country=~%s" % (slug, country)
    text = _get(url, opener).decode("utf-8", "replace")
    vals, name = {}, COUNTRY_NAMES.get(country, country)
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("code") == country and row.get(col) not in (None, ""):
            try:
                vals[int(row["year"])] = float(row[col]); name = row.get("entity") or name
            except ValueError:
                pass
    if not vals:
        raise NoData("Our World in Data has no figures for that")
    return {"title": "%s, %s" % (title, name), "unit": unit, "values": vals, "url": "https://ourworldindata.org/grapher/" + slug,
            "source": "Our World in Data (%s)" % slug}


def fetch_fred(sid, title, unit, opener=None):
    text = _get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + sid, opener, timeout=15).decode("utf-8", "replace")
    by = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and re.match(r"\d{4}-", row[0]):
            try:
                by.setdefault(int(row[0][:4]), []).append(float(row[1]))
            except ValueError:
                pass                                   # FRED writes "." for a missing value: a gap, not a zero
    vals = {y: round(sum(v) / len(v), 4) for y, v in by.items() if v}
    if not vals:
        raise NoData("FRED sent nothing usable")
    return {"title": title, "unit": unit, "values": vals, "url": "https://fred.stlouisfed.org/series/" + sid,
            "source": "Federal Reserve Bank of St. Louis, FRED (%s), annual average" % sid}


def fetch(ref, country=None, start=None, end=None, opener=None, cur=None):
    kind = ref[0]
    if kind == "curated":
        s = (cur or curated())[ref[1]]
        return dict(s)
    if kind == "bls":
        return fetch_bls(ref[1], ref[2], ref[3], start, end, opener)
    if kind == "wb":
        return fetch_worldbank(ref[1], ref[2], ref[3], country or "USA", opener)
    if kind == "owid":
        return fetch_owid(ref[1], ref[2], ref[3], ref[4], country or "USA", opener)
    if kind == "fred":
        return fetch_fred(ref[1], ref[2], ref[3], opener)
    raise NoData("I don't know that source")


def deflate(s, cpi, to_year=None):
    """Nominal dollars -> dollars of to_year (default: the latest full CPI year). Only $ series; years without CPI drop out."""
    cv = cpi["values"]
    full = [y for y in cv if y < max(cv)] or list(cv)
    ty = to_year if to_year in cv else max(full)
    out = {y: round(v * cv[ty] / cv[y], 4) for y, v in s["values"].items() if y in cv and cv[y]}
    r = dict(s, values=out, unit="%s (%d dollars)" % (s["unit"], ty), real=ty)
    r["title"] = s["title"] + ", inflation-adjusted"
    return r


def is_money(s):
    return "$" in (s.get("unit") or "") and "million" not in s.get("unit", "")


def clip(s, start=None, end=None):
    v = {y: x for y, x in s["values"].items() if (start is None or y >= start) and (end is None or y <= end)}
    return dict(s, values=v)


def build(query, opener=None, inflation=None, kind=None, cur=None, fetcher=None):
    """The words -> a chart dict ready to render, or NoData with what to say. Never invents a point."""
    q = _norm(query)
    start, end = years_of(q)
    infl = inflation if inflation is not None else bool(re.search(r"inflation|today'?s dollars|real (?:terms|dollars|prices)|adjusted", q))
    if re.search(r"\binflation\b", q) and not re.search(r"adjust|today'?s dollars|real (?:terms|dollars|prices)", q):
        infl = False                                    # "graph inflation" = the CPI itself
    if kind is None:
        kind = "bar" if re.search(r"\bbar(?: chart| graph)?\b|\bbars\b", q) else "line"
    parts = split_compare(q) if re.search(r"\bcompare\b|\bvs\.?\b|\bversus\b|\b(?:and|against)\b", q) else [q]
    topics = [(p, topic_of(p)) for p in parts]
    if len(parts) == 1 and not topics[0][1]:
        topics = [(q, llm_topic(q))]                     # the local model maps odd wording to a catalog key, or nothing
    if len(parts) > 1 and sum(1 for _, t in topics if t) < 2:
        # "population of france and germany": one topic, several countries
        t = topic_of(q)
        cs = [country_of(p) for p in parts]
        if t and sum(1 for c in cs if c) >= 2:
            topics = [(p, t) for p in parts]
        else:
            topics = [(q, topic_of(q))]
    cur = cur or curated()
    fetch_fn = fetcher or fetch
    series, missing = [], []
    for part, t in topics:
        if not t:
            missing.append(part)
            continue
        key, refs = t
        c = country_of(part) or country_of(q)
        if refs[0][0] in ("curated", "bls", "fred") and c and c != "USA":
            fb = [r for r in refs if r[-1] == "fallback" and r[0] in ("wb", "owid")]
            if not fb:
                missing.append(part + " (I only have U.S. figures for that)")
                continue
            refs = fb                                   # rc10: e.g. unemployment for another country from the World Bank
        got = False
        for ref in refs:
            if ref[-1] == "fallback" and got:
                continue                                # rc10: a fallback source only when the main one gave nothing
            try:
                s = fetch_fn(ref, country=c, start=start, end=end, opener=opener, cur=cur)
            except NoData as e:
                missing.append("%s (%s)" % (part, e)); continue
            except Exception as e:                      # offline, blocked, timed out: say so, invent nothing
                log.info("chart fetch %s: %s", ref, e)
                missing.append("%s (couldn't reach the source)" % part); continue
            s = clip(s, start, end)
            if s["values"]:
                series.append(s); got = True
    if not series:
        if missing and all("couldn't reach" in m for m in missing):
            raise NoData("I couldn't reach the data source just now, so I won't guess. Try again when we're online.")
        raise NoData("I couldn't find solid public data for that, so I won't make numbers up.")
    notes = []
    if infl:
        money = [s for s in series if is_money(s)]
        if money:
            series = [deflate(s, cur["cpi"]) if is_money(s) else s for s in series]
        else:
            notes.append("Not adjusted for inflation: these aren't prices.")
    units = {s["unit"] for s in series}
    indexed = False
    if len(series) > 1 and len(units) > 1 and not all(is_money(s) for s in series):
        series = [index100(s) for s in series]; indexed = True
        notes.append("Different units, so each line is shown as an index (its first year = 100).")
    lo = min(min(s["values"]) for s in series); hi = max(max(s["values"]) for s in series)
    if start and lo > start:
        notes.append("The free public data starts in %d; nothing solid before that." % lo)
    for s in series:
        if s.get("note"):
            notes.append(s["note"])
    title = series[0]["title"] if len(series) == 1 else compare_title(series)
    if series[0].get("key") == "milk_gallon" or (len(series) == 1 and series[0].get("key") == "milk_half_gallon"):
        title = "U.S. average milk price" + (", inflation-adjusted" if infl else "")
    srcs = []
    for s in series:
        if s["source"] not in srcs:
            srcs.append(s["source"])
    return {"title": title, "series": series, "kind": kind, "start": lo, "end": hi, "notes": notes, "sources": srcs,
            "missing": missing, "query": query, "inflation": bool(infl), "indexed": indexed,
            "unit": series[0]["unit"] if len(units) == 1 or not indexed else "index"}


def short(title):
    return title.split(",")[0].split(" (")[0].strip()


def compare_title(series):
    heads = [short(s["title"]) for s in series]
    if len(set(heads)) == 1:                 # "Population: France vs Germany"
        tails = [s["title"].split(",")[-1].strip() for s in series]
        return heads[0] + ": " + " vs ".join(tails)
    return " vs ".join(heads)


def index100(s):
    v = s["values"]
    if not v:
        return s
    b = v[min(v)]
    return dict(s, values={y: (x / b * 100 if b else 0) for y, x in v.items()}, unit="index, %d = 100" % min(v))


def fmt(v, unit=""):
    if "$" in unit and abs(v) < 1000:
        return "$%.2f" % v
    a = abs(v)
    for d, suf in ((1e12, " trillion"), (1e9, " billion"), (1e6, " million")):
        if a >= d:
            return ("$" if "$" in unit else "") + "%.1f%s" % (v / d, suf)
    if a >= 1000:
        return ("$" if "$" in unit else "") + "{:,.0f}".format(v)
    return ("%.1f" % v if a < 100 else "%.0f" % v) + ("%" if unit.strip().startswith("%") else "")


def summary(ch, sir="sir"):
    """One spoken sentence from the real first and last points."""
    s = ch["series"][0]
    v = s["values"]
    y0, y1 = min(v), max(v)
    a, b = v[y0], v[y1]
    what = short(s["title"])
    if len(ch["series"]) > 1 and len({short(x["title"]) for x in ch["series"]}) == 1 and \
            any(w in s.get("source", "") for w in ("World Bank", "Our World")):
        what = what + " in " + s["title"].split(",")[-1].strip()
    unit = s["unit"]
    ch_pct = ((b - a) / a * 100) if a else 0
    line = "%s went from %s in %d to %s in %d" % (what, fmt(a, unit), y0, fmt(b, unit), y1)
    if a and abs(ch_pct) >= 1:
        line += " (%s%.0f%%)" % ("up " if ch_pct > 0 else "down ", abs(ch_pct))
    if ch.get("inflation") and s.get("real"):
        line += ", in %d dollars" % s["real"]
    line += ", %s." % sir
    for o in ch["series"][1:]:
        if "old series" in o.get("title", "") and o["values"]:
            oy = min(o["values"])
            line += " The older half-gallon series starts at %s in %d." % (fmt(o["values"][oy], o["unit"]), oy)
    if ch["start"] and any("starts in" in n for n in ch["notes"]):
        line += " The free public data only goes back to %d." % ch["start"]
    return line


def source_line(ch):
    yrs = "%d\u2013%d" % (ch["start"], ch["end"])
    return "Source: " + "; ".join(ch["sources"]) + " \u00b7 " + yrs


# ------------------------------------------------------------------ rendering (Qt, no matplotlib)
FONT = "Rajdhani"     # bundled with JARVIS OS (core/theme.load_fonts); Qt falls back to the system sans
PALETTE = ("#2fe6d6", "#ff2d7e", "#ffc857", "#9b7bff")


def render(ch, w=1100, h=640, potato=False):
    from PySide6 import QtCore, QtGui
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(QtGui.QColor(4, 10, 12))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    teal = QtGui.QColor(PALETTE[0])
    g = QtGui.QRadialGradient(w * 0.5, h * 0.35, max(w, h) * 0.7)
    g.setColorAt(0, QtGui.QColor(10, 46, 48)); g.setColorAt(1, QtGui.QColor(3, 8, 10))
    p.fillRect(0, 0, w, h, g)
    pen = QtGui.QPen(QtGui.QColor(47, 230, 214, 150), 1.5); p.setPen(pen)
    p.drawRoundedRect(QtCore.QRectF(1, 1, w - 2, h - 2), 14, 14)
    f = QtGui.QFont(FONT, 15); f.setBold(True); f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.2)
    p.setFont(f); p.setPen(QtGui.QColor(233, 243, 245))
    p.drawText(QtCore.QRectF(28, 16, w - 56, 30), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, ch["title"].upper())
    f2 = QtGui.QFont(FONT, 10); p.setFont(f2); p.setPen(QtGui.QColor(47, 230, 214, 200))
    p.drawText(QtCore.QRectF(28, 44, w - 56, 20), QtCore.Qt.AlignLeft, ch.get("unit", ""))
    L, R, T, B = 86, w - 30, 78, h - 92
    ser = ch["series"]
    ys = sorted({y for s in ser for y in s["values"]})
    x0, x1 = ys[0], ys[-1] if ys[-1] > ys[0] else ys[0] + 1
    vals = [v for s in ser for v in s["values"].values()]
    lo, hi = min(vals), max(vals)
    lo = 0 if lo >= 0 else lo
    if hi == lo:
        hi = lo + 1
    step = _nice((hi - lo) / 5)
    hi = math.ceil(hi / step) * step
    X = lambda y: L + (y - x0) / (x1 - x0) * (R - L)
    Y = lambda v: B - (v - lo) / (hi - lo) * (B - T)
    p.setFont(QtGui.QFont(FONT, 9))
    v = lo
    while v <= hi + 1e-9:
        p.setPen(QtGui.QPen(QtGui.QColor(47, 230, 214, 34), 1)); p.drawLine(QtCore.QPointF(L, Y(v)), QtCore.QPointF(R, Y(v)))
        p.setPen(QtGui.QColor(190, 220, 222)); p.drawText(QtCore.QRectF(4, Y(v) - 9, L - 12, 18), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, fmt(v, ch.get("unit", "")))
        v += step
    xs = _nice(max(1, (x1 - x0) / 8))
    yy = int(math.ceil(x0 / xs) * xs)
    while yy <= x1:
        p.setPen(QtGui.QPen(QtGui.QColor(47, 230, 214, 22), 1)); p.drawLine(QtCore.QPointF(X(yy), T), QtCore.QPointF(X(yy), B))
        p.setPen(QtGui.QColor(190, 220, 222)); p.drawText(QtCore.QRectF(X(yy) - 30, B + 6, 60, 16), QtCore.Qt.AlignCenter, str(yy))
        yy += int(xs)
    p.setPen(QtGui.QPen(QtGui.QColor(47, 230, 214, 120), 1.2)); p.drawLine(QtCore.QPointF(L, B), QtCore.QPointF(R, B))
    for i, s in enumerate(ser):
        col = QtGui.QColor(PALETTE[i % len(PALETTE)])
        pts = sorted(s["values"].items())
        dashed = "old series" in s.get("title", "")
        if ch.get("kind") == "bar":
            n = max(1, len(ser)); bw = max(2.0, (R - L) / max(1, (x1 - x0 + 1)) * 0.8 / n)
            for y, val in pts:
                bx = X(y) - bw * n / 2 + i * bw
                grad = QtGui.QLinearGradient(0, Y(val), 0, B)
                c2 = QtGui.QColor(col); c2.setAlpha(60); grad.setColorAt(0, col); grad.setColorAt(1, c2)
                p.fillRect(QtCore.QRectF(bx, Y(val), bw * 0.9, B - Y(val)), grad)
            continue
        segs, segm = [], []
        prev = None
        for y, val in pts:                               # a missing year breaks the line: gaps stay gaps
            if prev is not None and y - prev > 1 and len(pts) > 3 and _step(pts) == 1:
                segs.append(segm); segm = []
            segm.append(QtCore.QPointF(X(y), Y(val))); prev = y
        segs.append(segm)
        for seg in segs:
            if len(seg) < 1:
                continue
            path = QtGui.QPainterPath(seg[0])
            for q in seg[1:]:
                path.lineTo(q)
            if not potato and not dashed:
                for wdt, a in ((9, 24), (5, 50)):
                    c = QtGui.QColor(col); c.setAlpha(a); p.setPen(QtGui.QPen(c, wdt, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)); p.drawPath(path)
                fill = QtGui.QPainterPath(path); fill.lineTo(seg[-1].x(), B); fill.lineTo(seg[0].x(), B); fill.closeSubpath()
                gr = QtGui.QLinearGradient(0, T, 0, B); c = QtGui.QColor(col); c.setAlpha(46 if i == 0 else 20); gr.setColorAt(0, c); c.setAlpha(0); gr.setColorAt(1, c)
                p.fillPath(fill, gr)
            pn = QtGui.QPen(col, 2.4, QtCore.Qt.DashLine if dashed else QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
            p.setPen(pn); p.drawPath(path)
            if len(seg) == 1:
                p.setBrush(col); p.drawEllipse(seg[0], 3, 3); p.setBrush(QtCore.Qt.NoBrush)
        if pts:                                          # the latest value, labelled
            y, val = pts[-1]
            p.setBrush(col); p.setPen(QtCore.Qt.NoPen); p.drawEllipse(QtCore.QPointF(X(y), Y(val)), 4.5, 4.5); p.setBrush(QtCore.Qt.NoBrush)
            p.setPen(col); p.setFont(QtGui.QFont(FONT, 10, QtGui.QFont.Bold))
            p.drawText(QtCore.QRectF(X(y) - 120, Y(val) - 26, 116, 18), QtCore.Qt.AlignRight, fmt(val, s["unit"]))
    if len(ser) > 1:                                     # legend
        lx = L + 10; ly = T + 4
        p.setFont(QtGui.QFont(FONT, 9))
        for i, s in enumerate(ser):
            col = QtGui.QColor(PALETTE[i % len(PALETTE)])
            p.setPen(QtGui.QPen(col, 3)); p.drawLine(QtCore.QPointF(lx, ly + 8 + i * 18), QtCore.QPointF(lx + 18, ly + 8 + i * 18))
            p.setPen(QtGui.QColor(233, 243, 245)); p.drawText(QtCore.QRectF(lx + 24, ly + i * 18, 560, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, s["title"])
    p.setFont(QtGui.QFont(FONT, 8)); p.setPen(QtGui.QColor(190, 220, 222, 220))
    foot = source_line(ch)
    if ch.get("notes"):
        foot += "\n" + " ".join(ch["notes"])
    p.drawText(QtCore.QRectF(28, B + 26, w - 56, h - B - 30), QtCore.Qt.AlignLeft | QtCore.Qt.TextWordWrap, foot)
    p.end()
    return img


def _step(pts):
    ys = [y for y, _ in pts]
    d = [b - a for a, b in zip(ys, ys[1:])]
    return min(d) if d else 1


def _nice(x):
    if x <= 0:
        return 1
    e = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if x <= m * e:
            return m * e
    return 10 * e


def ensure_app():
    """A QGuiApplication for offscreen rendering (the daemon has none); fonts registered when the bundle is there."""
    from PySide6 import QtGui, QtWidgets
    app = QtGui.QGuiApplication.instance()
    if app is None:
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication([])     # a QApplication (not just QGuiApplication) so widgets can share the process
    try:
        from .core import theme as T
        T.load_fonts()
    except Exception:
        pass
    return app


def save_png(ch, path, w=1600, h=930):
    ensure_app()
    img = render(ch, w, h)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not img.save(path, "PNG"):
        raise OSError("could not write " + path)
    return path


def slug(ch):
    s = re.sub(r"[^a-z0-9]+", "-", ch["title"].lower()).strip("-")[:48] or "chart"
    return "%s-%d-%d" % (s, ch["start"], ch["end"])


# ---------------------------------------------------------------- rc10: the JARVIS OS side (voice, state, window)
CHART_W = r"(?:graph|chart|plot|line graph|line chart|bar chart|bar graph|diagram)"
_NEG = re.compile(r"^(?:don'?t|do not|never|stop|no\b|not\b|why|how come|i (?:told|asked|said)|it (?:won'?t|didn'?t|doesn'?t|can'?t)|"
                  r"you (?:didn'?t|don'?t|can'?t|won'?t|never)|that (?:didn'?t|wasn'?t|isn'?t))")

def _clean_voice(low):
    t = (low or "").strip().lower().replace("\u2019", "'").strip(" .!?")
    t = re.sub(r"^(?:(?:hey |ok |okay )?jarvis[, ]+)", "", t)
    t = re.sub(r"^(?:(?:can you|could you|would you|will you|please|i want you to|i need you to|i'd like you to|go ahead and|"
               r"let's|lets) )+", "", t).strip()
    return re.sub(r"(?:,? please|,? for me|,? jarvis|,? now|,? thanks)+$", "", t).strip(" ,")

def parse_voice(low):
    """('chart', query, compare) | ('save', None, None) | ('mod', {'inflation': bool} | {'kind': 'bar'|'line'}, None) | None."""
    t = _clean_voice(low)
    if not t or _NEG.match(t) or not re.search(r"\b(?:graph|chart|plot|compare|inflation|today'?s dollars|diagram|how (?:has|have|did)|"
                                                     r"(?:nominal|original|actual|raw) (?:prices|dollars|numbers))\b", t):
        return None
    F = lambda rx: re.fullmatch(rx, t)
    if F(r"(?:save|keep|export|download)(?: (?:this|that|the|my))? (?:chart|graph|plot)(?: (?:as a )?(?:picture|png|image|file))?"
         r"(?: to (?:my )?(?:pictures|desktop|documents))?"):
        return ("save", None, None)
    if F(r"(?:now |and )?(?:adjust|correct)(?: (?:it|that|this|the chart|the graph|the prices?|them))? for inflation|"
         r"(?:show|make|do|put)(?: (?:it|that|this|the chart|the graph|them))? (?:in|with) (?:today'?s|current|real) (?:dollars|money|prices)|"
         r"inflation[- ]adjusted|(?:in )?today'?s dollars"):
        return ("mod", {"inflation": True}, None)
    if F(r"(?:take off|remove|turn off|undo|without)(?: the)? inflation(?: adjustment)?|"
         r"(?:show|go back to)(?: the)? (?:nominal|original|actual|raw) (?:prices|dollars|numbers)"):
        return ("mod", {"inflation": False}, None)
    m = F(r"(?:make|turn|change|switch|show)(?: (?:it|that|this|the chart|the graph))?(?: (?:into|to|as))? (?:a )?(bar|line)(?: chart| graph)?|"
          r"(?:as )?(?:a )?(bar|line) (?:chart|graph) instead")
    if m:
        return ("mod", {"kind": m.group(1) or m.group(2)}, None)
    if F(r"(?:close|hide|dismiss)(?: the| this| that)? (?:chart|graph|plot)s?"):
        return ("close", None, None)
    m = F(r"(?:show|give|get|make|draw|create|build|pull up|bring up|put up|do)?(?: me| us)?(?: an?| the| some)? ?(?:quick |live |data )?"
          + CHART_W + r"s?(?: of| for| on| showing| about| with| comparing| that shows|)? (.+)|"
          r"(?:how (?:has|have|did) (.+?) (?:changed|gone up|risen|grown|moved)(?: over time)?(?: since .+)?)")
    if m:
        q = m.group(1) or m.group(2)
        if not q or re.fullmatch(r"(?:it|that|this|them|twist|armor|the course|a course|my route)", q):
            return None
        if not topic_of(q) and not re.search(r"\b(?:graph|chart|plot)s? (?:of|for|showing|about|on)\b", t) and \
                not re.search(r"\b(?:price|prices|cost|costs|rate|rates|number|amount|since|over time|per year)\b|\b1[6-9]\d\d\b|\b20\d\d\b", q):
            return None                                   # "plot twist" is not a chart request
        return ("chart", t if m.group(2) else q, len(split_compare(q)) > 1)
    m = F(r"(?:compare|comparing)(?: the)? (.+?) (?:and|with|to|vs\.?|versus|against) (.+)|(.+?) (?:vs\.?|versus) (.+)")
    if m:
        a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if (topic_of(a) and topic_of(b)) or (topic_of(t) and country_of(a) and country_of(b)):
            return ("chart", t, True)
    return None

def state_dir(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".local", "state", "jarvis", "charts")

def save_last(ch, query, opts=None, home=None):
    d = state_dir(home); os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "last.json.tmp")
    with open(tmp, "w") as f:
        json.dump({"query": query, "opts": opts or {}, "chart": ch}, f)
    os.replace(tmp, os.path.join(d, "last.json"))
    return os.path.join(d, "last.json")

def from_json(ch):
    """JSON turns the year keys into strings; put them back so render()/summary() see numbers again."""
    for s in (ch or {}).get("series") or []:
        if isinstance(s.get("values"), dict):
            s["values"] = {int(k): v for k, v in s["values"].items()}
    return ch

def load_last(home=None):
    try:
        d = json.load(open(os.path.join(state_dir(home), "last.json")))
    except (OSError, ValueError):
        return {}
    if isinstance(d, dict) and d.get("chart"):
        from_json(d["chart"])
    return d if isinstance(d, dict) else {}

def pictures_dir(home=None):
    """~/Pictures/JARVIS Charts (the desktop's own Pictures folder when xdg-user-dir names one; a given home is used as is)."""
    given = home is not None
    home = home or os.path.expanduser("~")
    pics = os.path.join(home, "Pictures")
    if given:
        return os.path.join(pics, "JARVIS Charts")
    try:
        import subprocess
        r = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip() and r.stdout.strip() != home:
            pics = r.stdout.strip()
    except Exception:
        pass
    return os.path.join(pics, "JARVIS Charts")

def window_argv(path, accent=None):
    import sys
    a = [sys.executable, "-m", "jarvisd.chartwin", path]
    return a + (["--accent", accent] if accent else [])
