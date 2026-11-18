"""Match the people in a company profile (CEO, founders, leadership) to LinkedIn
profile links that the company's own web pages contain. No Claude calls.

A link is kept only when it clearly belongs to that person:
- the link text contains the person's first and last name, or
- the profile slug contains both names (linkedin.com/in/paul-le-floch-1a2b), or
- the name appears just before the link on the page (a team card) and the slug
  shares at least the first or the last name.
Opaque slugs with nothing to confirm them are dropped (precision over recall).
"""
import html as H
import re
import unicodedata
from urllib.parse import unquote, urljoin, urlparse

DROP = re.compile(r"<(script|style|noscript|svg|template|iframe)\b.*?</\1>", re.S | re.I)
ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.S | re.I)
TITLES = {"dr", "phd", "ph", "md", "mba", "msc", "bsc", "prof", "mr", "ms", "mrs", "jr", "sr", "ii", "iii",
          "peng", "frcs", "facs", "dphil", "rn"}
WINDOW = 400  # characters of page text looked at before a link


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def name_tokens(name: str) -> list[str]:
    return [t for t in norm(name.split(",")[0]).split() if t not in TITLES and len(t) > 1]


def profile_url(href: str) -> str | None:
    u = urlparse(href)
    if not u.netloc.endswith("linkedin.com"):
        return None
    m = re.match(r"/(?:mwlite/)?in/([^/?#]+)", u.path)
    return f"https://www.linkedin.com/in/{m.group(1)}" if m else None


def _slug_has(slug: str, tok: str) -> bool:
    parts = norm(unquote(slug)).split()
    return tok in parts or (len(tok) >= 4 and tok in "".join(parts))


def slug_full(slug: str, toks: list[str]) -> bool:
    if len(toks) < 2:
        return False
    first, last = toks[0], toks[-1]
    parts = norm(unquote(slug)).split()
    initial = first[0] in [p for p in parts if len(p) == 1] or "".join(parts).startswith(first[0] + last)
    return _slug_has(slug, last) and (_slug_has(slug, first) or initial)


def slug_some(slug: str, toks: list[str]) -> bool:
    return any(len(t) >= 3 and _slug_has(slug, t) for t in toks)


def anchors(page: str, base: str) -> tuple[str, list[dict]]:
    """Page text with each LinkedIn person link replaced by a marker, plus the links."""
    page = DROP.sub(" ", page)
    page = re.sub(r"<!--.*?-->", " ", page, flags=re.S)
    found = []

    def sub(m):
        url = profile_url(urljoin(base, H.unescape(m.group(1).strip())))
        label = H.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        if not url:
            return " " + label + " "
        found.append({"url": url, "label": label})
        return f" \x00{len(found) - 1}\x00 "

    page = ANCHOR.sub(sub, page)
    text = H.unescape(re.sub(r"<[^>]+>", " ", page))
    return re.sub(r"\s+", " ", text), found


def match_people(names: list[str], pages: list[tuple[str, str]]) -> dict[str, str]:
    """{person name: linkedin url} for the names we can tie to a link with confidence."""
    # the same person can appear as "Rotem Kopel, Ph.D." and "Rotem Kopel": key by name tokens
    people, spellings = {}, {}
    for n in names:
        toks = name_tokens(n or "")
        if toks:
            key = " ".join(toks)
            people[key] = toks
            spellings.setdefault(key, []).append(n)
    cands: dict[str, dict[str, int]] = {}  # name -> url -> strength (2 strong, 1 proximity)

    def add(name, url, strength):
        cur = cands.setdefault(name, {})
        cur[url] = max(cur.get(url, 0), strength)

    for page, base in pages:
        text, links = anchors(page, base)
        for i, a in enumerate(links):
            slug = a["url"].rsplit("/in/", 1)[1]
            lab = norm(a["label"])
            for name, toks in people.items():
                if len(toks) >= 2 and toks[0] in lab.split() and toks[-1] in lab.split():
                    add(name, a["url"], 2)
                elif slug_full(slug, toks):
                    add(name, a["url"], 2)
            # proximity: the text between the previous LinkedIn link and this one
            pos = text.find(f"\x00{i}\x00")
            prev = text.rfind("\x00", 0, pos - 1) if pos > 0 else -1
            window = norm(text[max(prev + 1, pos - WINDOW):pos])
            hits = [n for n, toks in people.items() if len(toks) >= 2 and " ".join(toks) in window]
            if len(hits) == 1 and slug_some(slug, people[hits[0]]):
                add(hits[0], a["url"], 1)

    # one link per person, one person per link; drop anything ambiguous
    best = {}
    for name, urls in cands.items():
        top = max(urls.values())
        winners = [u for u, s in urls.items() if s == top]
        if len(winners) == 1:
            best[name] = winners[0]
    owners: dict[str, list[str]] = {}
    for name, url in best.items():
        owners.setdefault(url, []).append(name)
    return {n: url for key, url in best.items() if len(owners[url]) == 1 for n in spellings[key]}


def people_names(p: dict) -> list[str]:
    names = [p.get("ceo", "")] + list(p.get("founders", [])) + [x.get("name", "") for x in p.get("leadership", [])]
    return [n.strip() for n in names if n and n.strip()]
