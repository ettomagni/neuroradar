"""Add companies that are new on the Neurofounders start-up map. No Claude calls.

Walks every page of https://www.neurofounders.co/resources/start-up-map, compares
the listed start-ups with data/companies.json and, for each new one, adds a record
(name, category, country, one-line description, website and technical fields from
its Neurofounders page). The pin starts at its country; the next steps of the
weekly run find its careers page, and `enrich_llm.py --missing-only` builds its
profile (HQ city, CEO, people's LinkedIn).

Companies that disappear from the map are only reported, never deleted.

Usage: python3 scripts/discover_companies.py [--dry-run]
"""
import hashlib
import html
import json
import math
import re
import sys
from datetime import datetime, timezone

from build_seed import CATEGORIES, COUNTRIES, slugify
from enrich_profiles import BASE, parse
from netutil import CACHE, DATA, Throttle, fetch, fetch_json, load_companies, save_json

MAP = "https://www.neurofounders.co/resources/start-up-map"
ITEM = re.compile(r'role="listitem" class="collection-item-7 w-dyn-item">(.*?)Read more', re.S)


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def field(block: str, cls: str) -> str:
    m = re.search(rf'class="{cls}"[^>]*>(.*?)</', block, re.S)
    return clean(m.group(1)) if m else ""


def map_entries(throttle: Throttle) -> list[dict]:
    out, url, seen_pages = [], MAP, 0
    while url and seen_pages < 30:
        throttle.wait()
        status, _, page = fetch(url)
        if status != 200:
            raise SystemExit(f"Start-up map: HTTP {status} on {url}")
        for block in ITEM.findall(page):  # each card ends with its "Read more" link to /startups/<slug>
            slug = re.search(r'href="/startups/([^"/?#]+)"', block)
            if not slug:
                continue
            tags = [clean(t) for t in re.findall(r'class="style-for-tags"[^>]*>(.*?)</', block, re.S)]
            # the card's one-liner is Neurofounders' own text: describe the company by its tags instead
            out.append({"slug": slug.group(1), "name": field(block, "heading-44"),
                        "description": " · ".join(t for t in tags if t) or field(block, "heading-46"),
                        "country": field(block, "heading-45"), "category": field(block, "heading-46")})
        nxt = re.search(r'<a[^>]*href="(\?[0-9a-f]+_page=\d+)"[^>]*class="[^"]*w-pagination-next', page) \
            or re.search(r'<a[^>]*class="[^"]*w-pagination-next[^"]*"[^>]*href="(\?[0-9a-f]+_page=\d+)"', page)
        url = MAP + nxt.group(1) if nxt else None
        seen_pages += 1
    return out


def country_point(country: str, key: str, cache: dict, throttle: Throttle):
    """Country centre plus a small deterministic offset (same idea as build_seed)."""
    if country in COUNTRIES:
        lat0, lng0, rmax = COUNTRIES[country]
    else:
        k = f"country|{country.lower()}"
        if k not in cache:
            throttle.wait()
            s, d = fetch_json(f"https://nominatim.openstreetmap.org/search?format=json&limit=1&country={country}")
            cache[k] = {"lat": float(d[0]["lat"]), "lng": float(d[0]["lon"])} if s == 200 and d else None
        if not cache[k]:
            return None
        lat0, lng0, rmax = cache[k]["lat"], cache[k]["lng"], 1.0
    h = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)
    r, th = rmax * 0.8 * ((h % 1000) / 1000) ** 0.5, (h // 1000) % 360
    return (round(lat0 + r * math.sin(math.radians(th)), 4),
            round(lng0 + r * math.cos(math.radians(th)) / math.cos(math.radians(lat0)), 4))


def main():
    dry = "--dry-run" in sys.argv
    throttle = Throttle(1.5)
    companies = load_companies()
    known = {c["neurofounders_slug"] for c in companies}
    ids = {c["id"] for c in companies}
    entries = map_entries(throttle)
    if len(entries) < 0.8 * len(known):  # layout change or partial page: do nothing
        raise SystemExit(f"Start-up map parse looks wrong ({len(entries)} entries vs {len(known)} known): skipping")
    on_map = {e["slug"] for e in entries}
    gone = sorted(c["name"] for c in companies if c["neurofounders_slug"] not in on_map)
    new = [e for e in entries if e["slug"] not in known]
    print(f"Start-up map: {len(entries)} companies · new: {len(new)} · no longer listed: {len(gone)}")
    if gone:
        print("  no longer on the map:", ", ".join(gone))
    if not new or dry:
        for e in new:
            print(f"  + {e['name']} · {e['country']} · {e['category']}")
        return

    geo = DATA / "geocode_cache.json"
    cache = json.loads(geo.read_text(encoding="utf-8")) if geo.exists() else {}
    nf_cache = CACHE / "neurofounders"
    nf_cache.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    for e in new:
        cid = slugify(e["name"]) or e["slug"]
        while cid in ids:
            cid += "-2"
        ids.add(cid)
        c = {"id": cid, "name": e["name"], "category": CATEGORIES.get(e["category"], "tools"),
             "country": e["country"], "description": e["description"], "neurofounders_slug": e["slug"],
             "status": "active", "parent": None, "website": None, "careers_url": None, "ats": None,
             "city": None, "added_at": today}
        throttle.wait()
        s, _, page = fetch(BASE + e["slug"])
        if s == 200:
            (nf_cache / f"{e['slug']}.html").write_text(page, encoding="utf-8")
            info = parse(page, e["name"])
            info.pop("analysis", None)  # Neurofounders' own text: never published
            c.update(info)
        pt = country_point(e["country"], cid, cache, throttle)
        if pt:
            c["lat"], c["lng"] = pt
        c["location_precision"] = "country"
        companies.append(c)
        print(f"  + {c['name']} · {c['country']} · {c['category']} · {c.get('website') or 'no website yet'}")
    save_json(geo, cache)
    save_json(DATA / "companies.json", companies)


if __name__ == "__main__":
    main()
