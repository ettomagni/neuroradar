"""Second pass for companies whose careers page did not reveal an ATS (JS-rendered
job lists, missing links…): try likely board tokens directly on the Greenhouse,
Lever and Ashby public APIs.

A hit is accepted only if it clearly belongs to the company:
  - Greenhouse: the board's own name matches the company name; or
  - the company name appears in the job texts/URLs, AND
  - the job texts mention neurotech vocabulary (guards against namesakes such as
    another "Atlas" or "Clarity").

Usage: python3 scripts/probe_ats.py
"""
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from ats import FETCHERS
from netutil import DATA, fetch_json, load_companies, save_json

NEURO = re.compile(r"neuro|brain|eeg|neural|bci|stimulat|cogniti|sleep|medical device|"
                   r"implant|ultrasound|mri|clinical|patient|biosignal|wearable", re.I)
SKIP = {"ats"}  # already resolved through the careers page


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def tokens_for(c: dict) -> list[str]:
    name = re.sub(r"\(.*?\)", "", c["name"]).strip()
    words = [w for w in re.split(r"[^A-Za-z0-9]+", unicodedata.normalize("NFKD", name)) if w]
    cands = [c["id"], c["id"].replace("-", ""), norm(name)]
    if words:
        cands.append(words[0].lower())
    host = urlparse(c.get("website") or "").netloc.lower().removeprefix("www.")
    if host:
        stem = host.split(".")[0]
        cands += [stem, stem.replace("-", "")]
    out = []
    for t in cands:
        if len(t) >= 3 and t not in out:
            out.append(t)
    return out


def name_keys(c: dict) -> list[str]:
    name = re.sub(r"\(.*?\)", "", c["name"]).strip()
    keys = {norm(name)}
    first = norm(name.split()[0]) if name.split() else ""
    if len(first) >= 5:
        keys.add(first)
    return [k for k in keys if k]


def strong_keys(c: dict) -> list[str]:
    """Identifiers that are hard to collide on: full name and website domain."""
    keys = {norm(re.sub(r"\(.*?\)", "", c["name"]))}
    host = urlparse(c.get("website") or "").netloc.lower().removeprefix("www.")
    if host:
        keys.add(host)  # e.g. "aweartech.com" inside texts/URLs
    return [k for k in keys if len(k) >= 4]


def belongs(c: dict, jobs: list[dict]) -> bool:
    """Neurotech vocabulary in most postings + the full name or the website domain."""
    if not jobs:
        return False
    sample = jobs[:15]
    blob = " ".join(f"{j['url']} {j['title']} {j['description'][:4000]}" for j in sample)
    flat, low = norm(blob), blob.lower()
    neuro_share = sum(bool(NEURO.search(j["title"] + " " + j["description"][:4000])) for j in sample) / len(sample)
    strong = any((k in low) if "." in k else (k in flat) for k in strong_keys(c))
    return strong and neuro_share >= 0.5


def probe(c: dict):
    rejected = {f"{r['type']}/{r['token']}" for r in c.get("ats_rejected", [])}
    for token in tokens_for(c):
        if any(f"{a}/{token}" in rejected for a in ("greenhouse", "ashby", "lever")):
            continue
        s, meta = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}")
        if s == 200 and meta:
            try:
                jobs = FETCHERS["greenhouse"](token)
            except Exception:
                jobs = []
            full_name = norm(meta.get("name", "")) == norm(re.sub(r"\(.*?\)", "", c["name"]))
            if (jobs and belongs(c, jobs)) or (full_name and not jobs):
                return {"type": "greenhouse", "token": token}, len(jobs)
        for ats in ("ashby", "lever"):
            try:
                jobs = FETCHERS[ats](token)
            except Exception:
                continue
            if belongs(c, jobs):
                return {"type": ats, "token": token}, len(jobs)
    return None, 0


def main():
    companies = load_companies()
    todo = [c for c in companies if c.get("careers_status") not in SKIP and c.get("website")]
    print(f"Probing {len(todo)} companies…", flush=True)
    found = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for c, (hit, n) in zip(todo, pool.map(probe, todo)):
            if hit:
                found += 1
                c["ats"] = hit
                c["careers_status"] = "ats"
                c["open_jobs_at_detection"] = n
                c["ats_found_by"] = "probe"
                print(f"  + {c['name']}: {hit['type']}/{hit['token']} ({n} jobs)", flush=True)
    save_json(DATA / "companies.json", companies)
    print(f"Found {found} more boards.")


if __name__ == "__main__":
    main()
