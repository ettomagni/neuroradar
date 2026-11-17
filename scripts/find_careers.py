"""Find each company's careers page and ATS, starting from its website.

For every company with a website: fetch the homepage, look for an ATS link or a
careers link, follow it, and detect the ATS there. Tries /careers and /jobs as
a fallback. A detected ATS is kept only if its public API answers.

Writes careers_url, ats {type, token} and careers_status into data/companies.json.
Companies already resolved are skipped unless --refresh is given.

Usage: python3 scripts/find_careers.py [--refresh] [--only <id>]
"""
import html
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from ats import FETCHERS, detect
from netutil import DATA, fetch, load_companies, save_json

KEYWORDS = ["career", "jobs", "job-openings", "join-us", "join us", "joinus", "join the team",
            "join our team", "work-with-us", "work with us", "hiring", "open positions",
            "open roles", "vacanc", "karriere", "stellen", "emplois", "carrières", "carrieres",
            "lavora con noi", "trabaja", "werken bij", "vacatures"]
# Statuses that are final; anything else (site down, not found…) is retried on the next run.
RESOLVED = {"ats", "custom_page", "ats_unsupported", "no_website"}
FALLBACK_PATHS =["/careers", "/jobs", "/join-us", "/company/careers", "/about/careers", "/career"]


def anchors(page: str, base: str):
    for m in re.finditer(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", page, re.S | re.I):
        href, text = m.group(1).strip(), html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        yield urljoin(base, href), re.sub(r"\s+", " ", text).strip().lower()


def careers_links(page: str, base: str) -> list[str]:
    host = urlparse(base).netloc.removeprefix("www.")
    scored = []
    for url, text in anchors(page, base):
        u = url.lower()
        hit = any(k in u for k in KEYWORDS) or any(k in text for k in KEYWORDS)
        if not hit:
            continue
        same = urlparse(url).netloc.removeprefix("www.").endswith(host)
        scored.append((0 if same else 1, len(url), url))
    seen, out = set(), []
    for _, _, url in sorted(scored):
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out[:4]


def verify(ats: str, token: str) -> int | None:
    """Return the number of open jobs if the ATS API answers, else None."""
    if ats not in FETCHERS:
        return None
    try:
        return len(FETCHERS[ats](token))
    except Exception:
        return None


def resolve(c: dict) -> dict:
    site = c.get("website")
    if not site:
        return {"careers_status": "no_website"}
    status, final, home = fetch(site)
    if status != 200:
        return {"careers_status": f"site_http_{status}"}

    candidates = [(final, home)]
    links = careers_links(home, final)
    for url in links:
        s, f, p = fetch(url)
        if s == 200:
            candidates.append((f, p))
    if not links:
        for path in FALLBACK_PATHS:
            s, f, p = fetch(urljoin(final, path))
            if s == 200 and urlparse(f).path.rstrip("/") not in ("", "/"):
                candidates.append((f, p))
                links = [f]
                break

    # Also treat a careers link that points straight at an ATS as a page to scan.
    for url, page in candidates:
        found = detect(url + "\n" + page)
        if found:
            ats, token = found
            n = verify(ats, token)
            if n is not None:
                return {"careers_url": links[0] if links else url, "ats": {"type": ats, "token": token},
                        "careers_status": "ats", "open_jobs_at_detection": n}
            if ats not in FETCHERS:  # e.g. Teamtailor/JOIN: known ATS, no fetcher yet
                return {"careers_url": links[0] if links else url, "ats": {"type": ats, "token": token},
                        "careers_status": "ats_unsupported"}
    if links:
        return {"careers_url": links[0], "ats": None, "careers_status": "custom_page"}
    return {"careers_status": "not_found"}


def main():
    refresh = "--refresh" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    companies = load_companies()
    todo = [c for c in companies
            if (only is None or c["id"] == only)
            and (refresh or only or c.get("careers_status") not in RESOLVED)]
    print(f"Resolving {len(todo)} companies…", flush=True)

    def work(c):
        try:
            return c, resolve(c)
        except Exception as e:  # never let one site kill the run
            return c, {"careers_status": f"error: {type(e).__name__}"}

    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for c, res in pool.map(work, todo):
            c.pop("ats", None); c.pop("careers_url", None); c.pop("open_jobs_at_detection", None)
            c.update(res)
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(todo)}", flush=True)
                save_json(DATA / "companies.json", companies)

    save_json(DATA / "companies.json", companies)
    from collections import Counter
    print(Counter(c.get("careers_status", "?") for c in companies).most_common())
    print(Counter((c.get("ats") or {}).get("type") for c in companies if c.get("ats")).most_common())


if __name__ == "__main__":
    main()
