"""Fact-check facility claims against the hospital's OWN website.

For each facility with an official website we fetch the homepage plus up to 4 internal pages whose
link text/URL suggests services (Services, Departments, Facilities, Specialities, …), then search the
page text with the same service rules (strong terms only, with negation handling) used on the record.

Outcome per (facility, service):
    confirmed   – the hospital's site names the service (quote + page URL kept)
    not_found   – site reachable, service not named (sites are often incomplete: NOT proof of absence)
    unreachable – site down / blocked / not HTML
Directory/aggregator domains (practo, justdial, business.site …) are skipped: they are not the
hospital's own voice. Domains shared by several facilities (hospital groups) are marked 'chain' and
count with lower weight, because the page may describe another branch.

Polite crawling: robots.txt respected, one site per worker, <= 5 pages per site, 10 s timeouts,
generic User-Agent.
"""

from __future__ import annotations

import gzip
import json
import re
import ssl
import time
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .evidence import _is_negated
from .services import SERVICES
from .text import norm, parse_list

USER_AGENT = "Mozilla/5.0 (compatible; FindCareVerifier/1.0; healthcare-access research)"
MAX_BYTES = 1_500_000
MAX_PAGES = 5
TIMEOUT = 10
DIRECTORY_DOMAINS = {
    "business.site", "practo.com", "g.page", "eka.care", "apollo247.com", "justdial.com", "facebook.com", "instagram.com",
    "lybrate.com", "google.com", "goo.gl", "maps.app.goo.gl", "sulekha.com", "indiamart.com", "linkedin.com", "twitter.com",
    "x.com", "youtube.com", "wikipedia.org", "en.wikipedia.org", "quikr.com", "credihealth.com", "medifee.com",
    "hexahealth.com", "bajajfinservhealth.in", "docindia.org", "clinicspots.com", "vaidam.com", "joonsquare.com",
    "sehat.com", "drlogy.com", "kivihealth.com", "linktr.ee", "wa.me", "blogspot.com", "wixsite.com",
}
LINK_HINT_RE = re.compile(
    r"servic|facilit|department|speciali|specialty|treatment|procedure|centre|center|unit|about|infrastructure|"
    r"dialysis|nephro|icu|nicu|emergency|trauma|maternity|obstetric|gyn|cardi|onco|cancer|radiolog|imaging|burn|neuro|"
    r"ortho|eye|blood|transplant|surgery|medical", re.I)
SKIP_EXT_RE = re.compile(r"\.(?:pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|pptx?|mp4|mp3)(?:$|\?)", re.I)


def site_of(official_website) -> tuple[str | None, str | None]:
    """-> (url, domain) from the officialWebsite field (which may be a list-like string)."""
    for x in parse_list(official_website):
        u = x.strip().strip('"').strip("'")
        if "." not in u or " " in u:
            continue
        if "//" not in u:
            u = "http://" + u
        try:
            dom = urlsplit(u).netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")
        except ValueError:
            continue
        if dom:
            return u, dom
    return None, None


def is_directory(domain) -> bool:
    if not isinstance(domain, str):
        return False
    return any(domain == d or domain.endswith("." + d) for d in DIRECTORY_DOMAINS)


class _TextAndLinks(HTMLParser):
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "section", "article", "span", "a"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self._skip, self._href, self._atext = [], [], 0, None, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._href, self._atext = dict(attrs).get("href"), []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._atext).strip()))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._atext.append(data)

    def text(self) -> str:
        t = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))
        return re.sub(r"\n\s*\n+", "\n", t).strip()


_CTX_UNVERIFIED = ssl.create_default_context()
_CTX_UNVERIFIED.check_hostname = False
_CTX_UNVERIFIED.verify_mode = ssl.CERT_NONE


def _get(url: str, timeout: int = TIMEOUT) -> tuple[str | None, str, str]:
    """-> (html or None, final_url, note). Retries once without certificate checks (many small
    hospital sites have broken TLS; we only read public pages)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"})
    for ctx, note in ((None, ""), (_CTX_UNVERIFIED, "tls_unverified")):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                ctype = r.headers.get("Content-Type", "")
                if "html" not in ctype.lower() and ctype:
                    return None, r.geturl(), f"not_html:{ctype[:40]}"
                raw = r.read(MAX_BYTES)
                charset = r.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="ignore"), r.geturl(), note
        except ssl.SSLError:
            continue
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLError) and ctx is None:
                continue
            return None, url, f"error:{str(getattr(e, 'reason', e))[:60]}"
        except Exception as e:  # timeouts, resets, bad status lines…
            return None, url, f"error:{type(e).__name__}"
    return None, url, "error:ssl"


def _robots(base: str) -> urllib.robotparser.RobotFileParser | None:
    rp = urllib.robotparser.RobotFileParser()
    txt, _, note = _get(urljoin(base, "/robots.txt"), timeout=6)
    if txt is None:
        return None  # no robots.txt (or unreachable) -> allowed
    rp.parse(txt.splitlines())
    return rp


def fetch_site(url: str, max_pages: int = MAX_PAGES) -> dict:
    """Fetch homepage + up to (max_pages-1) service-looking internal pages."""
    t0 = time.time()
    out = {"start_url": url, "status": "unreachable", "pages": [], "note": ""}
    try:
        rp = _robots(url)
    except Exception:
        rp = None
    if rp is not None and not rp.can_fetch(USER_AGENT, url):
        out.update(status="blocked_by_robots")
        return out
    html, final, note = _get(url)
    if html is None and url.startswith("http://"):
        html, final, note = _get("https://" + url[len("http://"):])
    if html is None:
        out["note"] = note
        return out
    p = _TextAndLinks()
    try:
        p.feed(html)
    except Exception:
        pass
    out["pages"].append({"url": final, "text": p.text()[:60000]})
    host = urlsplit(final).netloc.lower().removeprefix("www.")
    cands = {}
    for href, atext in p.links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        u = urljoin(final, href).split("#")[0]
        if SKIP_EXT_RE.search(u) or urlsplit(u).netloc.lower().removeprefix("www.") != host or u.rstrip("/") == final.rstrip("/"):
            continue
        hits = len(LINK_HINT_RE.findall(atext + " " + urlsplit(u).path))
        if hits:
            cands[u] = max(cands.get(u, 0), hits)
    for u in sorted(cands, key=lambda k: -cands[k])[: max_pages - 1]:
        if rp is not None and not rp.can_fetch(USER_AGENT, u):
            continue
        h, fu, _ = _get(u)
        if h:
            q = _TextAndLinks()
            try:
                q.feed(h)
            except Exception:
                pass
            out["pages"].append({"url": fu, "text": q.text()[:60000]})
    out.update(status="ok", note=note, seconds=round(time.time() - t0, 1))
    return out


_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")

# On marketing websites some record-level strong terms are too generic ("Medicine Delivery",
# "blood transfusions" in a disease explainer). For web confirmation these matched terms don't count.
WEB_GENERIC_TERMS = {
    "maternity": re.compile(r"^deliver(?:y|ies)$"),
    "blood_bank": re.compile(r"^transfusions?$"),
    "emergency": re.compile(r"^casualty$"),
}
# Long sentences only count when phrased as the hospital offering something (not patient-education text
# such as "common cardiac surgeries include heart transplants…"). Short menu items / headings always count.
OFFER_CUE_RE = re.compile(r"\b(?:we|our|us|offer(?:s|ed|ing)?|provid(?:e|es|ed|ing)|available|facilit(?:y|ies)|unit|units|department|dept|"
                          r"centre|center|services?|equipped|24 ?x ?7|24 hours|round the clock|dedicated|state of the art)\b", re.I)
LONG_CHUNK = 120
WEB_IGNORE_CHUNK_RE = re.compile(r"\b(?:journey|story|stories|blog|news|testimonial|advertisement for the post|vacanc|career)\b", re.I)


def find_services(pages: list[dict], services=SERVICES) -> list[dict]:
    """Strong-term mentions of each service in the site's pages (negated mentions excluded)."""
    found = {}
    for pg in pages:
        for chunk in _SPLIT.split(pg["text"]):
            chunk = chunk.strip()
            if len(chunk) < 3 or len(chunk) > 600:
                continue
            n = norm(chunk)
            if WEB_IGNORE_CHUNK_RE.search(chunk) or (len(chunk) > LONG_CHUNK and not OFFER_CUE_RE.search(chunk)):
                continue
            for s in services:
                if s.id in found:
                    continue
                m = s.strong_re().search(n)
                if m and s.id in WEB_GENERIC_TERMS and WEB_GENERIC_TERMS[s.id].match(m.group(0).strip()):
                    continue
                if m and not _is_negated(n, m):
                    found[s.id] = {"service_id": s.id, "term": m.group(0).strip(),
                                   "quote": re.sub(r"\s+", " ", chunk)[:240], "page_url": pg["url"]}
    return list(found.values())


def save_jsonl_gz(path: str, rows: list[dict], mode: str = "at"):
    with gzip.open(path, mode, encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_jsonl_gz(path: str) -> list[dict]:
    out = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # a partially written last line after an interrupted run
    except (FileNotFoundError, EOFError):
        pass
    return out
