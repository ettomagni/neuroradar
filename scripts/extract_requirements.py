"""Claude extracts structured requirements from each open role.

Reads data/jobs.json + the posting text (data/job_texts.json from fetch_jobs, or the
role's own page for roles found on custom careers pages). Writes
data/requirements.json {job_key: {...}}; roles already extracted are skipped.

Many pending roles (> BATCH_MIN) go through the Message Batches API (50% cheaper,
asynchronous). If a batch is still running when the time budget ends, its id is
saved in data/requirements_batch.json and collected on the next run.

Usage: python3 scripts/extract_requirements.py [--limit N] [--no-batch] [--wait-min M]
"""
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from claude_client import MODEL, USAGE, can_batch, has_key, make_client, parse_response, schema_format, structured
from netutil import DATA, fetch, load_companies, save_json
from pagetext import page_to_text

BATCH_MIN = 30
MIN_TEXT = 300

SYSTEM = """You extract structured hiring requirements from a job posting of a neurotechnology company.
Be faithful to the posting: do not guess what is not written.
- seniority: infer from title and requirements (intern, junior = 0-2 yrs, mid = 3-5, senior = 5+, lead/staff/principal = lead, head/director/VP = director/executive).
- min_years_experience: the smallest number of years explicitly required; -1 if not stated.
- degree_required: the minimum degree explicitly required ("none" if the posting says no degree needed, "unknown" if not stated). degree_fields: fields named for the degree.
- required_skills / preferred_skills: short canonical names (e.g. "Python", "EEG signal processing", "PCB design", "ISO 13485", "PyTorch"), max 12 each. Required = must-have; preferred = nice-to-have / bonus.
- domain: the main function of the role.
- work_mode: remote / hybrid / onsite / unknown, from the posting.
- visa_sponsorship: yes / no / not_stated (citizenship or "must be authorized to work without sponsorship" = no).
- salary: the range as written, or "".
- summary: one short sentence on what the person will do."""

SCHEMA = {
    "type": "object",
    "properties": {
        "seniority": {"type": "string", "enum": ["intern", "junior", "mid", "senior", "lead", "director", "executive", "unknown"]},
        "employment_type": {"type": "string", "enum": ["full-time", "part-time", "internship", "contract", "phd", "postdoc", "unknown"]},
        "work_mode": {"type": "string", "enum": ["remote", "hybrid", "onsite", "unknown"]},
        "degree_required": {"type": "string", "enum": ["none", "bachelor", "master", "phd", "md", "unknown"]},
        "degree_fields": {"type": "array", "items": {"type": "string"}},
        "min_years_experience": {"type": "integer"},
        "required_skills": {"type": "array", "items": {"type": "string"}},
        "preferred_skills": {"type": "array", "items": {"type": "string"}},
        "domain": {"type": "string", "enum": ["hardware_engineering", "software_engineering", "ml_ai", "research_science",
                                              "clinical", "regulatory_quality", "manufacturing_operations", "product_design",
                                              "sales_marketing", "business_operations", "other"]},
        "visa_sponsorship": {"type": "string", "enum": ["yes", "no", "not_stated"]},
        "salary": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["seniority", "employment_type", "work_mode", "degree_required", "degree_fields",
                 "min_years_experience", "required_skills", "preferred_skills", "domain",
                 "visa_sponsorship", "salary", "summary"],
    "additionalProperties": False,
}


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def user_msg(job, company, text):
    return (f"Company: {company['name']} ({company.get('modality', '')}, {company.get('category', '')})\n"
            f"Role: {job['title']}\nLocation: {job.get('location', '')}\n\nPosting:\n{text[:24000]}")


def posting_text(job, texts):
    t = texts.get(job["key"], "")
    if len(t) >= MIN_TEXT:
        return t
    if job.get("ats") == "page" and job.get("url"):
        s, final, page = fetch(job["url"])
        if s == 200:
            return page_to_text(page, final, limit=24000)[0]
    return t


def cid(key):  # batch custom_id must be [A-Za-z0-9_-]{1,64}
    return hashlib.sha1(key.encode()).hexdigest()[:32]


def finish(entry_out, job, text):
    return {**entry_out, "text_hash": hashlib.sha1(text.encode()).hexdigest()[:12],
            "extracted_at": now_iso(), "model": MODEL}


def collect_batch(client, reqs, state, jobs_by_key, texts_cache):
    batch = client.messages.batches.retrieve(state["id"])
    if batch.processing_status != "ended":
        return False
    for r in client.messages.batches.results(state["id"]):
        key = state["keys"].get(r.custom_id)
        if not key or r.result.type != "succeeded":
            continue
        msg = r.result.message
        USAGE.add(msg.usage, batch=True)
        try:
            out = parse_response(msg)
        except Exception:
            continue
        if out is not None and key in jobs_by_key:
            reqs[key] = finish(out, jobs_by_key[key], texts_cache.get(key, ""))
    return True


def main():
    args = sys.argv[1:]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    wait_min = float(args[args.index("--wait-min") + 1]) if "--wait-min" in args else 20
    if not has_key():
        print("No Claude credentials: skipping requirements extraction.")
        return
    client = make_client()
    companies = {c["id"]: c for c in load_companies()}
    jobs = load("jobs.json", [])
    jobs_by_key = {j["key"]: j for j in jobs}
    texts = load("job_texts.json", {})
    reqs = load("requirements.json", {})
    reqs = {k: v for k, v in reqs.items() if k in jobs_by_key}  # drop closed roles
    started = now_iso()
    stats = {"extracted": 0, "no_text": 0, "errors": 0, "mode": "direct"}

    # 1) collect a batch left over from a previous run
    pending_state = load("requirements_batch.json", None)
    if pending_state and can_batch():
        if collect_batch(client, reqs, pending_state, jobs_by_key, {}):
            (DATA / "requirements_batch.json").unlink()
            print("Collected previous batch.")
        else:
            print("Previous batch still running: try again next run.")
            save_json(DATA / "requirements.json", reqs)
            return

    todo = [j for j in jobs if j["key"] not in reqs][:limit]
    print(f"Pending roles: {len(todo)}", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        bodies = list(pool.map(lambda j: posting_text(j, texts), todo))
    work = []
    for j, t in zip(todo, bodies):
        if len(t) < MIN_TEXT:
            reqs[j["key"]] = {"status": "no_text", "extracted_at": now_iso()}
            stats["no_text"] += 1
        else:
            work.append((j, t))

    if len(work) > BATCH_MIN and "--no-batch" not in args and can_batch():
        stats["mode"] = "batch"
        keys = {cid(j["key"]): j["key"] for j, _ in work}
        texts_cache = {j["key"]: t for j, t in work}
        batch = client.messages.batches.create(requests=[
            Request(custom_id=cid(j["key"]), params=MessageCreateParamsNonStreaming(
                model=MODEL, max_tokens=4000, system=SYSTEM,
                messages=[{"role": "user", "content": user_msg(j, companies[j["company_id"]], t)}],
                output_config={"effort": "low", "format": schema_format(SCHEMA)},
            )) for j, t in work])
        state = {"id": batch.id, "keys": keys, "created_at": now_iso()}
        print(f"Batch {batch.id}: {len(work)} requests, waiting up to {wait_min:.0f} min…", flush=True)
        deadline = time.monotonic() + wait_min * 60
        done = False
        while time.monotonic() < deadline:
            if collect_batch(client, reqs, state, jobs_by_key, texts_cache):
                done = True
                break
            time.sleep(30)
        if not done:
            save_json(DATA / "requirements_batch.json", state)
            print("Batch not finished yet: saved, it will be collected on the next run.")
        stats["extracted"] = sum(1 for j, _ in work if j["key"] in reqs)
    else:
        def one(item):
            j, t = item
            try:
                out = structured(client, SYSTEM, user_msg(j, companies[j["company_id"]], t), SCHEMA, max_tokens=4000)
                return j, t, out, None
            except Exception as e:
                return j, t, None, f"{type(e).__name__}: {str(e)[:120]}"
        with ThreadPoolExecutor(max_workers=4) as pool:
            for j, t, out, err in pool.map(one, work):
                if err:
                    stats["errors"] += 1
                    print(f"  ! {j['title']}: {err}", flush=True)
                elif out is not None:
                    reqs[j["key"]] = finish(out, j, t)
                    stats["extracted"] += 1

    save_json(DATA / "requirements.json", reqs)
    log = load("runs.json", [])
    log.append({"at": started, "kind": "requirements", **stats, "usage": USAGE.summary()})
    save_json(DATA / "runs.json", log[-90:])
    print(f"Done: {stats} · est. cost ${USAGE.cost():.2f}")


if __name__ == "__main__":
    main()
