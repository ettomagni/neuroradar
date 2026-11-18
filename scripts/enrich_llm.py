"""Company profiles from each company's own website, extracted by Claude.

For every company: render the homepage and up to two "about / team / leadership"
pages in headless Chromium, then Claude extracts HQ city, CEO, founders,
leadership, technology, products, conditions, team size and milestones — only
what the pages state. The HQ city is geocoded (OpenStreetMap Nominatim, cached in
data/geocode_cache.json) so the pin moves from the country centre to the city.

Bulk runs use the Message Batches API (50% cheaper); an unfinished batch is saved
in data/profile_batch.json and collected on the next run.

Usage: python3 scripts/enrich_llm.py [--limit N] [--only ID] [--no-batch] [--refresh] [--wait-min M]
"""
import hashlib
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from claude_client import MODEL, USAGE, backend, can_batch, token_check, has_key, make_client, parse_response, schema_format, structured
from netutil import DATA, Throttle, fetch_json, load_companies, save_json
from pagetext import page_to_text
from render import render_many

ABOUT_KEYS = re.compile(r"about|team|leadership|management|company|who-we-are|our-story|founders|people|board", re.I)
REFRESH_DAYS = 90
BATCH_MIN = 20

SYSTEM = """You build a factual profile of a neurotechnology company from pages of its own website.
Use only what the pages state. Never guess names, numbers or places; when the pages do not say, leave the field empty (an empty string or an empty list), never write placeholders like "unknown" or quotes.
- hq_city / hq_country: the headquarters (or main office) as written on the site.
- ceo: the person the site names as CEO (or "Chief Executive Officer"); "" if not named.
- founders: people the site calls founders/co-founders.
- leadership: up to 6 key people with their role as written.
- technology: 2-3 plain-English sentences, in your own words, on how the technology works and what it does.
- products: up to 4 named products/platforms with a one-line description each.
- conditions: medical conditions or use cases targeted.
- team_size: only if stated (e.g. "50+ employees"), else "".
- milestones: up to 4 notable facts stated on the site (regulatory clearances, trials, funding rounds, awards), short.
- linkedin_url: the company's LinkedIn page if linked on the pages, else ""."""

SCHEMA = {
    "type": "object",
    "properties": {
        "hq_city": {"type": "string"},
        "hq_country": {"type": "string"},
        "ceo": {"type": "string"},
        "founders": {"type": "array", "items": {"type": "string"}},
        "leadership": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
            "required": ["name", "role"], "additionalProperties": False}},
        "technology": {"type": "string"},
        "products": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            "required": ["name", "description"], "additionalProperties": False}},
        "conditions": {"type": "array", "items": {"type": "string"}},
        "team_size": {"type": "string"},
        "milestones": {"type": "array", "items": {"type": "string"}},
        "linkedin_url": {"type": "string"},
    },
    "required": ["hq_city", "hq_country", "ceo", "founders", "leadership", "technology", "products",
                 "conditions", "team_size", "milestones", "linkedin_url"],
    "additionalProperties": False,
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def about_links(links: set[str], home: str) -> list[str]:
    host = urlparse(home).netloc.removeprefix("www.")
    cands = []
    for u in links:
        pu = urlparse(u)
        if pu.netloc.removeprefix("www.") != host or not ABOUT_KEYS.search(pu.path):
            continue
        if re.search(r"\.(pdf|jpg|png|zip)$|/blog/|/news/|/press/|/careers?|/jobs?", pu.path, re.I):
            continue
        cands.append((len(pu.path), u.split("#")[0]))
    out = []
    for _, u in sorted(cands):
        if u not in out:
            out.append(u)
    return out[:2]


def gather_pages(companies: list[dict]) -> dict[str, tuple[str, set[str]]]:
    """Render homepages, then their about/team pages. Returns {id: (text, links)}."""
    homes = render_many([c["website"] for c in companies])
    second, texts = {}, {}
    for c in companies:
        r = homes.get(c["website"])
        if not r or r.status != 200:
            continue
        t, links = page_to_text(r.html, r.final_url, limit=8000)
        texts[c["id"]] = [f"# Homepage {r.final_url}\n{t}"], links
        second[c["id"]] = about_links(links, r.final_url)
    extra = render_many([u for us in second.values() for u in us])
    out = {}
    for cid, (parts, links) in texts.items():
        for u in second.get(cid, []):
            r = extra.get(u)
            if r and r.status == 200:
                t, l2 = page_to_text(r.html, r.final_url, limit=10000)
                parts.append(f"# Page {r.final_url}\n{t}")
                links |= l2
        out[cid] = ("\n\n".join(parts), links)
    return out


PLACEHOLDERS = {'""', "''", "n/a", "na", "none", "unknown", "not stated", "not specified", "not mentioned", "-", "—", "null"}


def blank(v):
    """Turn placeholder answers ('""', 'unknown', …) into real empties, recursively."""
    if isinstance(v, str):
        v = v.strip()
        return "" if v.lower() in PLACEHOLDERS else v
    if isinstance(v, list):
        out = [blank(x) for x in v]
        return [x for x in out if x and (not isinstance(x, dict) or x.get("name"))]
    if isinstance(v, dict):
        return {k: blank(x) for k, x in v.items()}
    return v


def clean_profile(p: dict, links: set[str]) -> dict:
    p = blank(p)
    # "Tokyo / California" -> "Tokyo": the first place named is the one we geocode
    p["hq_city"] = re.split(r"\s*(?:/|;|\||\band\b)\s*", p.get("hq_city", ""))[0].strip(" ,")
    li = p.get("linkedin_url", "").strip()
    p["linkedin_url"] = li if li in links and "linkedin.com" in li else ""
    p["leadership"] = p.get("leadership", [])[:6]
    p["products"] = p.get("products", [])[:4]
    p["milestones"] = p.get("milestones", [])[:4]
    p["source"] = "company website"
    p["updated_at"] = now_iso()
    p["model"] = MODEL
    return p


# ---------- geocoding ----------
_geo_throttle = Throttle(1.1)


def geocode(city: str, country: str, cache: dict):
    key = f"{city}|{country}".lower()
    if key in cache:
        return cache[key]
    _geo_throttle.wait()
    q = urllib.parse.urlencode({"q": f"{city}, {country}", "format": "json", "limit": 1})
    s, d = fetch_json(f"https://nominatim.openstreetmap.org/search?{q}")
    res = None
    if s == 200 and d:
        res = {"lat": round(float(d[0]["lat"]), 4), "lng": round(float(d[0]["lon"]), 4)}
    cache[key] = res
    return res


def apply_profiles(companies, profiles: dict):
    cache = load("geocode_cache.json", {})
    for c in companies:
        p = profiles.get(c["id"])
        if not p:
            continue
        c["profile"] = p
        if p.get("hq_city"):
            c["city"] = p["hq_city"]
            g = geocode(p["hq_city"], p.get("hq_country") or c["country"], cache)
            if g:
                # tiny deterministic offset so companies in the same city don't overlap
                h = int(hashlib.sha1(c["id"].encode()).hexdigest()[:6], 16)
                c["lat"] = round(g["lat"] + ((h % 100) - 50) / 2500, 4)
                c["lng"] = round(g["lng"] + (((h // 100) % 100) - 50) / 2500, 4)
                c["location_precision"] = "city"
    save_json(DATA / "geocode_cache.json", cache)


def main():
    args = sys.argv[1:]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    only = args[args.index("--only") + 1] if "--only" in args else None
    wait_min = float(args[args.index("--wait-min") + 1]) if "--wait-min" in args else 30
    if "--repair" in args:  # re-clean stored profiles + re-geocode, no Claude calls
        companies = load_companies()
        profiles = {}
        for c in companies:
            if c.get("profile"):
                keep = {k: c["profile"][k] for k in ("source", "updated_at", "model") if k in c["profile"]}
                links = {c["profile"]["linkedin_url"]} if c["profile"].get("linkedin_url") else set()
                profiles[c["id"]] = {**clean_profile(dict(c["profile"]), links), **keep}
                if not profiles[c["id"]]["hq_city"]:
                    c.pop("city", None)
        apply_profiles(companies, profiles)
        save_json(DATA / "companies.json", companies)
        print(f"Repaired {len(profiles)} profiles")
        return
    if not has_key():
        print("No Claude credentials: skipping profile enrichment.")
        return
    if backend() == "cli":
        print("Claude backend: subscription (Claude Code) ·", token_check(), flush=True)
    client = make_client()
    companies = load_companies()
    started = now_iso()
    stats = {"profiles": 0, "no_pages": 0, "errors": 0, "mode": "direct"}

    pending = load("profile_batch.json", None)
    if pending and can_batch():
        batch = client.messages.batches.retrieve(pending["id"])
        if batch.processing_status != "ended":
            print("Previous profile batch still running: try again later.")
            return
        profiles = {}
        for r in client.messages.batches.results(pending["id"]):
            cid = pending["ids"].get(r.custom_id)
            if cid and r.result.type == "succeeded":
                USAGE.add(r.result.message.usage, batch=True)
                try:
                    out = parse_response(r.result.message)
                except Exception:
                    continue
                if out:
                    profiles[cid] = clean_profile(out, set(pending["links"].get(cid, [])))
        apply_profiles(companies, profiles)
        save_json(DATA / "companies.json", companies)
        (DATA / "profile_batch.json").unlink()
        print(f"Collected previous batch: {len(profiles)} profiles · est. cost ${USAGE.cost():.2f}")
        return

    stale = (datetime.now(timezone.utc) - timedelta(days=REFRESH_DAYS)).isoformat()
    todo = [c for c in companies if c.get("website") and only in (None, c["id"])
            and ("--refresh" in args or (c.get("profile") or {}).get("updated_at", "") < stale)][:limit]
    print(f"Profiles to build: {len(todo)} · rendering websites…", flush=True)
    pages = gather_pages(todo)
    work = [(c, *pages[c["id"]]) for c in todo if c["id"] in pages and len(pages[c["id"]][0]) > 300]
    stats["no_pages"] = len(todo) - len(work)

    def user_msg(c, text):
        return f"Company: {c['name']} ({c['website']})\nKnown country: {c['country']}\n\nWebsite pages:\n{text}"

    profiles = {}
    if len(work) > BATCH_MIN and "--no-batch" not in args and can_batch():
        stats["mode"] = "batch"
        ids = {hashlib.sha1(c["id"].encode()).hexdigest()[:32]: c["id"] for c, _, _ in work}
        rev = {v: k for k, v in ids.items()}
        batch = client.messages.batches.create(requests=[
            Request(custom_id=rev[c["id"]], params=MessageCreateParamsNonStreaming(
                model=MODEL, max_tokens=4000, system=SYSTEM,
                messages=[{"role": "user", "content": user_msg(c, text)}],
                output_config={"effort": "low", "format": schema_format(SCHEMA)},
            )) for c, text, _ in work])
        state = {"id": batch.id, "ids": ids, "created_at": now_iso(),
                 "links": {c["id"]: sorted(l for l in links if "linkedin.com" in l) for c, _, links in work}}
        print(f"Batch {batch.id}: {len(work)} requests, waiting up to {wait_min:.0f} min…", flush=True)
        deadline = time.monotonic() + wait_min * 60
        while time.monotonic() < deadline:
            b = client.messages.batches.retrieve(batch.id)
            if b.processing_status == "ended":
                break
            time.sleep(30)
        else:
            save_json(DATA / "profile_batch.json", state)
            print("Batch not finished: saved, run again later to collect it.")
            return
        links_by = {c["id"]: links for c, _, links in work}
        for r in client.messages.batches.results(batch.id):
            cid = ids.get(r.custom_id)
            if cid and r.result.type == "succeeded":
                USAGE.add(r.result.message.usage, batch=True)
                try:
                    out = parse_response(r.result.message)
                except Exception:
                    stats["errors"] += 1
                    continue
                if out:
                    profiles[cid] = clean_profile(out, links_by[cid])
    else:
        from concurrent.futures import ThreadPoolExecutor

        def one(item):
            c, text, links = item
            try:
                return c, links, structured(client, SYSTEM, user_msg(c, text), SCHEMA, max_tokens=4000), None
            except Exception as e:
                return c, links, None, f"{type(e).__name__}: {str(e)[:120]}"
        with ThreadPoolExecutor(max_workers=4) as pool:
            for c, links, out, err in pool.map(one, work):
                if err:
                    stats["errors"] += 1
                    print(f"  ! {c['name']}: {err}", flush=True)
                elif out:
                    profiles[c["id"]] = clean_profile(out, links)

    stats["profiles"] = len(profiles)
    apply_profiles(companies, profiles)
    save_json(DATA / "companies.json", companies)
    for cid, p in list(profiles.items())[:8]:
        print(f"  + {cid}: {p['hq_city']}, {p['hq_country']} · CEO: {p['ceo'] or '—'} · {p['technology'][:90]}", flush=True)
    log = load("runs.json", [])
    log.append({"at": started, "kind": "profiles", **stats, "usage": USAGE.summary()})
    save_json(DATA / "runs.json", log[-90:])
    print(f"Done: {stats} · est. cost ${USAGE.cost():.2f}")


if __name__ == "__main__":
    main()
