"""Daily careers job: pull open positions from every company's ATS and diff
them against yesterday.

Outputs (all in data/):
  jobs.json         open positions shown by the site (no long descriptions)
  job_texts.json    full plain-text descriptions, keyed like jobs (for phase 3 CV matching)
  jobs_closed.json  positions that disappeared, kept 60 days
  runs.json         one line of stats per run (last 90 runs)

A company whose ATS call fails keeps yesterday's positions (nothing is marked
closed on an error). The very first run is a baseline: its positions are not
flagged as new.

Usage: python3 scripts/fetch_jobs.py
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from ats import FETCHERS
from netutil import DATA, load_companies, save_json

import json


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def key(company_id, job):
    return f"{company_id}:{job['ats']}:{job['external_id']}"


def infer_remote(job):
    if job.get("remote") is not None:
        return "remote" if job["remote"] else None
    text = f"{job.get('location','')} {job.get('title','')}".lower()
    if "remote" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    return None


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_s = now.isoformat()
    companies = load_companies()
    targets = [c for c in companies if (c.get("ats") or {}).get("type") in FETCHERS]

    prev_jobs = load("jobs.json", None)
    baseline = prev_jobs is None
    prev = {j["key"]: j for j in (prev_jobs or [])}
    texts = load("job_texts.json", {})
    closed = load("jobs_closed.json", [])

    def work(c):
        try:
            return c, FETCHERS[c["ats"]["type"]](c["ats"]["token"]), None
        except Exception as e:
            return c, None, f"{type(e).__name__}: {e}"[:200]

    jobs, errors, ok_ids = [], {}, set()
    with ThreadPoolExecutor(max_workers=6) as pool:
        for c, fetched, err in pool.map(work, targets):
            if err:
                errors[c["id"]] = err
                continue
            ok_ids.add(c["id"])
            seen = set()
            for j in fetched:
                k = key(c["id"], j)
                if k in seen or not j["title"]:
                    continue
                seen.add(k)
                old = prev.get(k)
                texts[k] = j.pop("description", "")
                jobs.append({
                    "key": k,
                    "company_id": c["id"],
                    **{f: j[f] for f in ("ats", "external_id", "title", "url", "location",
                                         "department", "employment_type", "posted_at")},
                    "remote": infer_remote(j),
                    "first_seen_at": old["first_seen_at"] if old else now_s,
                    "last_seen_at": now_s,
                    "baseline": old.get("baseline", False) if old else baseline,
                })

    current = {j["key"] for j in jobs}
    # Keep yesterday's jobs for companies that errored today (or lost their ATS config).
    for k, j in prev.items():
        if k not in current and j["company_id"] not in ok_ids:
            jobs.append(j)
            current.add(k)
    newly_closed = [dict(j, closed_at=now_s) for k, j in prev.items() if k not in current]
    cutoff = (now - timedelta(days=60)).isoformat()
    closed = [j for j in closed + newly_closed if j["closed_at"] >= cutoff]
    texts = {k: v for k, v in texts.items() if k in current}

    jobs.sort(key=lambda j: (j["first_seen_at"], j["company_id"], j["title"]), reverse=True)
    new_today = [j for j in jobs if j["first_seen_at"] == now_s and not j["baseline"]]

    save_json(DATA / "jobs.json", jobs)
    save_json(DATA / "job_texts.json", texts)
    save_json(DATA / "jobs_closed.json", closed)
    runs = load("runs.json", [])
    runs.append({
        "at": now_s, "kind": "careers", "baseline": baseline,
        "companies_checked": len(targets), "companies_ok": len(ok_ids),
        "errors": errors, "jobs_open": len(jobs), "jobs_new": len(new_today),
        "jobs_closed": len(newly_closed),
    })
    save_json(DATA / "runs.json", runs[-90:])

    print(f"{'BASELINE ' if baseline else ''}run {now_s}: {len(ok_ids)}/{len(targets)} companies ok, "
          f"{len(jobs)} open, {len(new_today)} new, {len(newly_closed)} closed, {len(errors)} errors")
    names = {c["id"]: c["name"] for c in companies}
    for j in new_today[:40]:
        print(f"  NEW  {names[j['company_id']]}: {j['title']} ({j['location']})")
    for cid, e in list(errors.items())[:20]:
        print(f"  ERR  {names[cid]}: {e}")
    print("By company:", Counter(names[j["company_id"]] for j in jobs).most_common(8))


if __name__ == "__main__":
    main()
