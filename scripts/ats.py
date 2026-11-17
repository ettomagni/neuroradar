"""ATS detection + job fetchers (Greenhouse, Lever, Ashby, Workable, Personio,
Recruitee, SmartRecruiters, BambooHR, Breezy). Each fetcher returns a list of
normalized jobs or raises RuntimeError."""
import html
import re
import xml.etree.ElementTree as ET

from netutil import fetch, fetch_json

# (ats, regex, group for token) — checked in order on the raw HTML.
PATTERNS = [
    ("greenhouse", r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)"),
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)"),
    ("lever", r"jobs\.((?:eu\.)?)lever\.co/([\w.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.%-]+)"),
    ("workable", r"apply\.workable\.com/([\w-]+)"),
    ("workable", r"//([\w-]+)\.workable\.com"),
    ("personio", r"//([\w-]+)\.jobs\.personio\.(?:de|com)"),
    ("recruitee", r"//([\w-]+)\.recruitee\.com"),
    ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)"),
    ("bamboohr", r"//([\w-]+)\.bamboohr\.com"),
    ("breezy", r"//([\w-]+)\.breezy\.hr"),
    ("teamtailor", r"//([\w-]+)\.teamtailor\.com"),
    ("join", r"join\.com/companies/([\w-]+)"),
]
BAD_TOKENS = {"embed", "api", "j", "www", "app", "apply", "careers", "jobs", "static",
              "assets", "cdn", "js", "v1", "widget", "boards", "job-boards", "career"}


def detect(page: str) -> tuple[str, str] | None:
    """Return (ats, token) for the first ATS reference found in the page."""
    page = page.replace("\\/", "/")
    for ats, rx in PATTERNS:
        for m in re.finditer(rx, page, re.I):
            if ats == "lever":
                region, token = m.group(1), m.group(2)
                token = f"eu:{token}" if region else token
            else:
                token = m.group(1)
            if token.lower().split(":")[-1] not in BAD_TOKENS:
                return ats, token
    return None


def _clean(htmltext: str | None, limit: int = 20000) -> str:
    if not htmltext:
        return ""
    t = html.unescape(htmltext)
    t = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "• ", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t).strip()
    return t[:limit]


def _job(ats, ext_id, title, url, location="", department="", remote=None,
         employment_type="", posted_at=None, description=""):
    return {
        "ats": ats, "external_id": str(ext_id), "title": (title or "").strip(),
        "url": url, "location": (location or "").strip(), "department": (department or "").strip(),
        "remote": remote, "employment_type": employment_type or "", "posted_at": posted_at,
        "description": description,
    }


def _need(status, data, what):
    if status != 200 or data is None:
        raise RuntimeError(f"{what}: HTTP {status}")
    return data


def greenhouse(token):
    s, d = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    d = _need(s, d, "greenhouse")
    return [_job("greenhouse", j["id"], j.get("title"), j.get("absolute_url"),
                 (j.get("location") or {}).get("name", ""),
                 ", ".join(x["name"] for x in j.get("departments") or []),
                 posted_at=j.get("first_published") or j.get("updated_at"),
                 description=_clean(j.get("content"))) for j in d.get("jobs", [])]


def lever(token):
    host = "api.lever.co"
    if token.startswith("eu:"):
        host, token = "api.eu.lever.co", token[3:]
    s, d = fetch_json(f"https://{host}/v0/postings/{token}?mode=json")
    d = _need(s, d, "lever")
    out = []
    for j in d:
        cat = j.get("categories") or {}
        lists = "\n\n".join(f"{l.get('text','')}\n{_clean(l.get('content'))}" for l in j.get("lists") or [])
        desc = "\n\n".join(x for x in [j.get("descriptionPlain"), lists, j.get("additionalPlain")] if x)
        created = j.get("createdAt")
        out.append(_job("lever", j["id"], j.get("text"), j.get("hostedUrl"),
                        cat.get("location", ""), cat.get("team", ""),
                        remote=(j.get("workplaceType") == "remote") if j.get("workplaceType") else None,
                        employment_type=cat.get("commitment", ""),
                        posted_at=_ms_to_iso(created), description=desc[:20000]))
    return out


def ashby(token):
    s, d = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    d = _need(s, d, "ashby")
    return [_job("ashby", j["id"], j.get("title"), j.get("jobUrl"), j.get("location", ""),
                 j.get("department") or j.get("team") or "", remote=j.get("isRemote"),
                 employment_type=j.get("employmentType", ""), posted_at=j.get("publishedAt"),
                 description=(j.get("descriptionPlain") or "")[:20000])
            for j in d.get("jobs", []) if j.get("isListed", True)]


def workable(token):
    s, d = fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true")
    d = _need(s, d, "workable")
    return [_job("workable", j.get("shortcode"), j.get("title"), j.get("url") or j.get("shortlink"),
                 ", ".join(x for x in [j.get("city"), j.get("country")] if x), j.get("department", ""),
                 remote=j.get("telecommuting"), employment_type=j.get("employment_type", ""),
                 posted_at=j.get("published_on"), description=_clean(j.get("description")))
            for j in d.get("jobs", [])]


def personio(token):
    for tld in ("de", "com"):
        s, _, text = fetch(f"https://{token}.jobs.personio.{tld}/xml")
        if s == 200 and "<position" in text:
            break
    else:
        raise RuntimeError(f"personio: HTTP {s}")
    root = ET.fromstring(text)
    out = []
    for p in root.findall("position"):
        g = lambda k: (p.findtext(k) or "").strip()
        desc = "\n\n".join(f"{d.findtext('name') or ''}\n{_clean(d.findtext('value'))}"
                           for d in p.findall("jobDescriptions/jobDescription"))
        out.append(_job("personio", g("id"), g("name"),
                        f"https://{token}.jobs.personio.{tld}/job/{g('id')}",
                        g("office"), g("department"), employment_type=g("employmentType"),
                        posted_at=g("createdAt") or None, description=desc[:20000]))
    return out


def recruitee(token):
    s, d = fetch_json(f"https://{token}.recruitee.com/api/offers/")
    d = _need(s, d, "recruitee")
    return [_job("recruitee", j["id"], j.get("title"), j.get("careers_url"),
                 j.get("location", ""), j.get("department", ""), remote=j.get("remote"),
                 employment_type=j.get("employment_type_code", ""), posted_at=j.get("published_at"),
                 description=_clean((j.get("description") or "") + (j.get("requirements") or "")))
            for j in d.get("offers", [])]


def smartrecruiters(token):
    s, d = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100")
    d = _need(s, d, "smartrecruiters")
    out = []
    for j in d.get("content", []):
        loc = j.get("location") or {}
        out.append(_job("smartrecruiters", j["id"], j.get("name"),
                        f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                        ", ".join(x for x in [loc.get("city"), loc.get("country")] if x),
                        (j.get("department") or {}).get("label", ""), remote=loc.get("remote"),
                        employment_type=(j.get("typeOfEmployment") or {}).get("label", ""),
                        posted_at=j.get("releasedDate")))
    return out


def bamboohr(token):
    s, d = fetch_json(f"https://{token}.bamboohr.com/careers/list")
    d = _need(s, d, "bamboohr")
    out = []
    for j in d.get("result", []):
        loc = j.get("location") or {}
        out.append(_job("bamboohr", j["id"], j.get("jobOpeningName"),
                        f"https://{token}.bamboohr.com/careers/{j['id']}",
                        ", ".join(x for x in [loc.get("city"), loc.get("state")] if x),
                        j.get("departmentLabel", ""), remote=bool(j.get("isRemote")) if j.get("isRemote") is not None else None,
                        employment_type=j.get("employmentStatusLabel", "")))
    return out


def breezy(token):
    s, d = fetch_json(f"https://{token}.breezy.hr/json")
    d = _need(s, d, "breezy")
    out = []
    for j in d:
        loc = j.get("location") or {}
        out.append(_job("breezy", j["id"], j.get("name"), j.get("url"),
                        loc.get("name", ""), (j.get("department") or ""),
                        remote=loc.get("is_remote"), employment_type=(j.get("type") or {}).get("name", ""),
                        posted_at=j.get("published_date")))
    return out


FETCHERS = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable,
    "personio": personio, "recruitee": recruitee, "smartrecruiters": smartrecruiters,
    "bamboohr": bamboohr, "breezy": breezy,
}


def _ms_to_iso(ms):
    if not ms:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
