"""Turn an HTML page into compact readable text, keeping links as `text <url>`
so Claude can return real URLs (which we then check against the page)."""
import hashlib
import html
import re
from urllib.parse import urljoin

DROP = re.compile(r"<(script|style|noscript|svg|template|iframe)\b.*?</\1>", re.S | re.I)
ANCHOR = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.S | re.I)
BLOCK = re.compile(r"<(br|/p|/li|/h\d|/div|/tr|/section|/article|/ul|/ol|/header|/footer)[^>]*>", re.I)


def page_to_text(page: str, base: str, limit: int = 30000) -> tuple[str, set[str]]:
    """Return (text, set of absolute link URLs present in the page)."""
    page = DROP.sub(" ", page)
    page = re.sub(r"<!--.*?-->", " ", page, flags=re.S)
    links: set[str] = set()

    def link(m):
        href = m.group(1).strip()
        if href.startswith(("mailto:", "tel:", "javascript:")):
            return m.group(2)
        url = urljoin(base, html.unescape(href))
        links.add(url)
        label = re.sub(r"<[^>]+>", " ", m.group(2))
        label = re.sub(r"\s+", " ", html.unescape(label)).strip()
        return f" {label} <{url}> " if label else f" <{url}> "

    page = ANCHOR.sub(link, page)
    page = BLOCK.sub("\n", page)
    page = re.sub(r"<li[^>]*>", "\n• ", page, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    lines = [l.strip() for l in text.splitlines()]
    text = "\n".join(l for l in lines if l)
    return text[:limit], links


def stable_hash(text: str) -> str:
    """Hash that ignores digits (dates, counters, cache-busters) so a page only
    counts as changed when its words change."""
    return hashlib.sha1(re.sub(r"\d+", "#", text).encode()).hexdigest()[:16]
