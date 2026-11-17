"""Claude reads careers pages that have no standard ATS.

1. discover: for companies whose careers page was not found, Claude picks the
   careers link from the homepage's own links (only real links are accepted).
2. read: for companies with a custom careers page, Claude extracts the listed
   roles. Pages are re-read only when their text changed (digit-insensitive hash),
   so daily runs cost little. If the page points to an ATS board we can read,
   the company is switched to that ATS.

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
  jobs_on_external_site = the roles live on another site (an ATS board, Wellfound, LinkedIn…): put that link in external_jobs_url;
  not_a_careers_page = the page is not about jobs.
- A spontaneous/open application is not a role: do not list it in jobs."""

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "page_kind": {"type": "string", "enum": ["job_listings", "no_open_roles", "general_application_only",
                                                  "jobs_on_external_site", "not_a_careers_page"]},
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


def read_page(client, c: dict, url: str, hop: int = 0):
    """Return (entry dict, company updates)."""
    status, final, page = fetch(url)
    if status != 200:
        return None, {}
    text, links = page_to_text(page, final)
    h = stable_hash(text)
    if len(text) < 200:
        return {"hash": h, "checked_at": now_iso(), "page_kind": "empty_page", "jobs": []}, {}

    out = structured(client, READ_SYSTEM,
                     f"Company: {c['name']}\nPage URL: {final}\n\nPage text:\n{text}",
                     READ_SCHEMA, max_tokens=12000)
    if out is None:
        return {"hash": h, "checked_at": now_iso(), "page_kind": "refused", "jobs": []}, {}

    ext = out["external_jobs_url"].strip()
    if out["page_kind"] == "jobs_on_external_site" and ext:
        hit = try_ats(ext)
        if hit:  # switch the company to the ATS reader
            return None, {"ats": {"type": hit[0], "token": hit[1]}, "careers_status": "ats",
                          "open_jobs_at_detection": hit[2], "ats_found_by": "claude"}
        if hop == 0 and ext.startswith("http"):
            entry, upd = read_page(client, c, ext, hop=1)
            if entry is not None:
                entry["hash"] = h  # keep tracking changes of the company's own page
                entry["source_url"] = ext
            return entry, upd

    jobs, seen = [], set()
    for j in out["jobs"]:
        title = j["title"].strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        jurl = j["url"].strip()
        jobs.append({**j, "title": title, "url": jurl if jurl in links else final})
    return {"hash": h, "checked_at": now_iso(), "page_kind": out["page_kind"], "jobs": jobs,
            "source_url": final}, {}


def main():
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    force = "--force" in args
    if not has_key():
        print("ANTHROPIC_API_KEY not set: skipping Claude careers reading.")
        return
    client = make_client()
    companies = load_companies()
    page_jobs = json.loads((DATA / "page_jobs.json").read_text()) if (DATA / "page_jobs.json").exists() else {}
    started = now_iso()

    if "--discover" in args:
        month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        todo = [c for c in companies if c.get("careers_status") == "not_found" and c.get("website")
                and (only in (None, c["id"])) and c.get("llm_checked_at", "") < month_ago][:limit]
        print(f"Discover: {len(todo)} companies", flush=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for c, upd in zip(todo, pool.map(lambda c: _safe(discover, client, c), todo)):
                c.update(upd or {})
                if (upd or {}).get("careers_url"):
                    print(f"  + {c['name']}: {upd['careers_url']}", flush=True)
        save_json(DATA / "companies.json", companies)

    todo = [c for c in companies if c.get("careers_status") == "custom_page" and c.get("careers_url")
            and (only in (None, c["id"]))][:limit]
    print(f"Read: {len(todo)} careers pages", flush=True)

    def work(c):
        prev = page_jobs.get(c["id"])
        if prev and not force:
            # cheap pre-check: skip Claude when the page text did not change
            s, final, page = fetch(c["careers_url"])  # the stored hash is of the company's own page
            if s == 200 and stable_hash(page_to_text(page, final)[0]) == prev["hash"]:
                return c, "unchanged", None
        return c, "read", _safe(read_page, client, c, c["careers_url"])

    stats = {"read": 0, "unchanged": 0, "errors": 0, "switched_to_ats": 0, "roles": 0}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for c, what, res in pool.map(work, todo):
            if what == "unchanged":
                stats["unchanged"] += 1
                continue
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
                if entry["jobs"]:
                    print(f"  + {c['name']}: {len(entry['jobs'])} roles ({entry['page_kind']})", flush=True)

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
        print(f"  ! {a[-1]['name'] if isinstance(a[-1], dict) else ''}: {type(e).__name__}: {str(e)[:160]}", flush=True)
        return None


if __name__ == "__main__":
    main()
