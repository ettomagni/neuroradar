"""Claude reads careers pages that have no standard ATS.

1. discover: for companies whose careers page was not found, Claude picks the
   careers link from the homepage's own links (only real links are accepted).
2. read: every custom careers page is opened in headless Chromium (job lists
   are often loaded by JavaScript). If the page loads an ATS we can read, the
   company is switched to that ATS (free from then on). Otherwise Claude
   extracts the roles, but only when the page text changed (digit-insensitive
   hash), so daily runs cost little.

Output: data/page_jobs.json  {company_id: {hash, checked_at, page_kind, jobs: [...]}}

Usage: python3 scripts/careers_llm.py [--discover] [--limit N] [--only ID] [--force]
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from ats import FETCHERS, detect
from claude_client import USAGE, has_key, make_client, structured
from netutil import DATA, fetch, load_companies, save_json
from pagetext import page_to_text, stable_hash
from render import Rendered, available as can_render, render_many

import json

READ_SYSTEM = """You read the careers page of a neurotechnology company and list the open roles on it.
Rules:
- Only list roles that are actually advertised on this page. Never invent roles.
- Use the exact role title. For "url", copy the role's link exactly as written in <...> on the page, or "" if the role has no own link.
- location: city/country as written ("" if not stated). work_mode: remote / hybrid / onsite / unknown.
- page_kind:
  job_listings = the page lists one or more specific roles;
  no_open_roles = careers page with no roles open right now;
  general_application_only = only an open/spontaneous application;
  jobs_on_external_site = the roles live on another site or sub-page (an ATS board, Wellfound, LinkedIn, a "see all vacancies" page…): put that link in external_jobs_url;
  roles_not_visible = the page says roles are open but the list itself is not in the text;
  not_a_careers_page = the page is not about jobs.
- A spontaneous/open application is not a role: do not list it in jobs."""

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "page_kind": {"type": "string", "enum": ["job_listings", "no_open_roles", "general_application_only",
                                                  "jobs_on_external_site", "roles_not_visible", "not_a_careers_page"]},
        "external_jobs_url": {"type": "string"},
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "location": {"type": "string"},
                    "department": {"type": "string"},
                    "employment_type": {"type": "string"},
                    "work_mode": {"type": "string", "enum": ["remote", "hybrid", "onsite", "unknown"]},
                },
                "required": ["title", "url", "location", "department", "employment_type", "work_mode"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["page_kind", "external_jobs_url", "jobs"],
    "additionalProperties": False,
}

DISCOVER_SYSTEM = """You get the list of links found on a company's homepage.
Pick the single link that leads to the company's careers / jobs / "join us" page, if there is one.
Answer with the URL copied exactly from the list, or "" if none of the links is about jobs."""

DISCOVER_SCHEMA = {
    "type": "object",
    "properties": {"careers_url": {"type": "string"},
                   "confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]}},
    "required": ["careers_url", "confidence"],
    "additionalProperties": False,
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def try_ats(url: str):
    """If url references a readable ATS board, return (ats, token, n_jobs)."""
    found = detect(url)
    if not found:
        s, _, page = fetch(url)
        found = detect(url + "\n" + page) if s == 200 else None
    if found and found[0] in FETCHERS:
        try:
            return found[0], found[1], len(FETCHERS[found[0]](found[1]))
        except Exception:
            return None
    return None


def discover(client, c: dict) -> dict:
    status, final, home = fetch(c["website"])
    if status != 200:
        return {"llm_checked_at": now_iso()}
    _, links = page_to_text(home, final)
    host = urlparse(final).netloc.removeprefix("www.")
    cands = sorted(l for l in links if l.startswith("http"))[:250]
    if not cands:
        return {"llm_checked_at": now_iso()}
    listing = "\n".join(cands)
    out = structured(client, DISCOVER_SYSTEM,
                     f"Company: {c['name']} ({host})\nLinks on the homepage:\n{listing}",
                     DISCOVER_SCHEMA, max_tokens=2000)
    res = {"llm_checked_at": now_iso()}
    if out and out["careers_url"] in links and out["confidence"] in ("high", "medium"):
        res.update(careers_url=out["careers_url"], careers_status="custom_page", careers_found_by="claude")
    return res


def get_page(url: str) -> Rendered:
    if can_render():
        return render_many([url])[url]
    status, final, page = fetch(url)
    return Rendered(status, final, page, [])


def ats_from_render(r: Rendered):
    """ATS referenced by the page or by the requests it made while loading."""
    found = detect("\n".join(r.requests) + "\n" + r.html)
    if found and found[0] in FETCHERS:
        try:
            return found[0], found[1], len(FETCHERS[found[0]](found[1]))
        except Exception:
            return None
    return None


def switch(hit):
    return {"ats": {"type": hit[0], "token": hit[1]}, "careers_status": "ats",
            "open_jobs_at_detection": hit[2], "ats_found_by": "render"}


def read_page(client, c: dict, r: Rendered, hop: int = 0):
    """Return (entry dict or None, company updates)."""
    text, links = page_to_text(r.html, r.final_url)
    h = stable_hash(text)
    if len(text) < 200:
        return {"hash": h, "checked_at": now_iso(), "page_kind": "empty_page", "jobs": []}, {}

    out = structured(client, READ_SYSTEM,
                     f"Company: {c['name']}\nPage URL: {r.final_url}\n\nPage text:\n{text}",
                     READ_SCHEMA, max_tokens=12000)
    if out is None:
        return {"hash": h, "checked_at": now_iso(), "page_kind": "refused", "jobs": []}, {}

    ext = out["external_jobs_url"].strip()
    if out["page_kind"] == "jobs_on_external_site" and ext.startswith("http") and hop == 0:
        hit = try_ats(ext)
        if hit:
            return None, switch(hit)
        r2 = get_page(ext)
        if r2.status == 200:
            hit = ats_from_render(r2)
            if hit:
                return None, switch(hit)
            entry, upd = read_page(client, c, r2, hop=1)
            if entry is not None:
                entry["hash"] = h  # keep tracking the company's own page
            return entry, upd

    jobs, seen = [], set()
    for j in out["jobs"]:
        title = j["title"].strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        jurl = j["url"].strip()
        jobs.append({**j, "title": title, "url": jurl if jurl in links else r.final_url})
    return {"hash": h, "checked_at": now_iso(), "page_kind": out["page_kind"], "jobs": jobs,
            "source_url": r.final_url}, {}


def main():
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    force = "--force" in args
    client = make_client() if has_key() else None
    if client is None:
        print("ANTHROPIC_API_KEY not set: rendering + ATS detection only, no Claude reading.")
    companies = load_companies()
    page_jobs = json.loads((DATA / "page_jobs.json").read_text()) if (DATA / "page_jobs.json").exists() else {}
    started = now_iso()

    if "--discover" in args and client:
        month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        todo = [c for c in companies if c.get("careers_status") == "not_found" and c.get("website")
                and (only in (None, c["id"])) and c.get("llm_checked_at", "") < month_ago][:limit]
        print(f"Discover: {len(todo)} companies (Claude picks the careers link from homepage links)", flush=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for c, upd in zip(todo, pool.map(lambda c: _safe(discover, client, c), todo)):
                c.update(upd or {})
                if (upd or {}).get("careers_url"):
                    print(f"  + {c['name']}: {upd['careers_url']}", flush=True)
        save_json(DATA / "companies.json", companies)
        print(f"Discover done · est. cost ${USAGE.cost():.2f}")
        return  # pages are read by the daily (non --discover) run

    todo = [c for c in companies if c.get("careers_status") == "custom_page" and c.get("careers_url")
            and (only in (None, c["id"]))][:limit]
    print(f"Rendering {len(todo)} pages{'' if can_render() else ' (plain fetch: Playwright not installed)'}…", flush=True)
    urls = [c["careers_url"] for c in todo]
    if can_render():
        rendered = render_many(urls)
    else:
        rendered = {u: Rendered(*fetch(u), []) for u in urls}

    stats = {"read": 0, "unchanged": 0, "errors": 0, "switched_to_ats": 0, "roles": 0}
    to_read = []
    for c in todo:
        r = rendered[c["careers_url"]]
        if r.status != 200:
            stats["errors"] += 1
            continue
        hit = ats_from_render(r)
        if hit:
            c.update(switch(hit))
            page_jobs.pop(c["id"], None)
            stats["switched_to_ats"] += 1
            print(f"  ~ {c['name']}: page loads {hit[0]}/{hit[1]} ({hit[2]} roles), switched", flush=True)
            continue
        prev = page_jobs.get(c["id"])
        if prev and not force and stable_hash(page_to_text(r.html, r.final_url)[0]) == prev["hash"]:
            stats["unchanged"] += 1
            continue
        to_read.append((c, r))

    if client is None:
        to_read = []
    print(f"Claude reads {len(to_read)} changed pages…", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda cr: (cr[0], _safe(read_page, client, cr[0], cr[1])), to_read))
    for c, res in results:
        if res is None:
            stats["errors"] += 1
            continue
        entry, upd = res
        stats["read"] += 1
        if upd:
            c.update(upd)
            page_jobs.pop(c["id"], None)
            stats["switched_to_ats"] += 1
            print(f"  ~ {c['name']}: switched to {upd['ats']['type']}/{upd['ats']['token']}", flush=True)
        elif entry is not None:
            page_jobs[c["id"]] = entry
            stats["roles"] += len(entry["jobs"])
            print(f"  · {c['name']}: {entry['page_kind']} ({len(entry['jobs'])} roles)", flush=True)

    save_json(DATA / "companies.json", companies)
    save_json(DATA / "page_jobs.json", page_jobs)
    log = json.loads((DATA / "runs.json").read_text()) if (DATA / "runs.json").exists() else []
    log.append({"at": started, "kind": "careers_llm", **stats, "usage": USAGE.summary()})
    save_json(DATA / "runs.json", log[-90:])
    print(f"Done: {stats} · est. cost ${USAGE.cost():.2f}")


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        name = next((x["name"] for x in a if isinstance(x, dict) and "name" in x), "")
        print(f"  ! {name}: {type(e).__name__}: {str(e)[:160]}", flush=True)
        return None


if __name__ == "__main__":
    main()
