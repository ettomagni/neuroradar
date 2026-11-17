"""Enrich data/companies.json from each company's Neurofounders profile page.

Adds: website, founded, funding_stage, modality, form_factor, interface_depth,
indication, target_user, regulatory_stage, analysis, related.
Raw pages are cached in data/cache/neurofounders/ so re-runs are free.

Usage: python3 scripts/enrich_profiles.py [--refresh]
"""
import html
import re
import sys

from netutil import CACHE, DATA, Throttle, fetch, load_companies, save_json

BASE = "https://www.neurofounders.co/startups/"
FIELDS = {
    "FOUNDED": "founded",
    "MODALITY": "modality",
    "FORM FACTOR": "form_factor",
    "INTERFACE DEPTH": "interface_depth",
    "INDICATION": "indication",
    "TARGET USER": "target_user",
    "REGULATORY STAGE": "regulatory_stage",
}


def text_lines(page: str) -> list[str]:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "\n", t)).replace("\xa0", " ")
    return [re.sub(r"\s+", " ", l).strip() for l in t.splitlines() if l.strip()]


def parse(page: str, name: str) -> dict:
    out: dict = {}
    m = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(?:(?!</a>).)*Visit website', page, re.S)
    if m:
        out["website"] = m.group(1).strip()

    lines = text_lines(page)
    for i, line in enumerate(lines[:-1]):
        key = line.upper()
        if key in FIELDS and FIELDS[key] not in out:
            out[FIELDS[key]] = lines[i + 1]
        elif line.startswith("$") and "Funding" in line and "funding_stage" not in out:
            out["funding_stage"] = lines[i + 1].strip(" -")

    # Analysis paragraph: the long text right before "Related companies".
    if "Related companies" in lines:
        j = lines.index("Related companies")
        if j and len(lines[j - 1]) > 120:
            out["analysis"] = lines[j - 1]
        related = []
        for l in lines[j + 1:j + 12]:
            if l in ("•", "Mentioned in Market Notes"):
                break
            related.append(l)
        out["related"] = related
    for k, v in list(out.items()):
        if isinstance(v, str) and v in ("-", "—", ""):
            del out[k]
    return out


def main() -> None:
    refresh = "--refresh" in sys.argv
    companies = load_companies()
    cache = CACHE / "neurofounders"
    cache.mkdir(parents=True, exist_ok=True)
    throttle = Throttle(1.0)
    missing = []
    analyses: dict[str, str] = {}

    for n, c in enumerate(companies, 1):
        f = cache / f"{c['neurofounders_slug']}.html"
        if refresh or not f.exists():
            throttle.wait()
            status, _, page = fetch(BASE + c["neurofounders_slug"])
            if status != 200:
                missing.append((c["name"], status))
                print(f"[{n}/{len(companies)}] {c['name']}: HTTP {status}", flush=True)
                continue
            f.write_text(page, encoding="utf-8")
        info = parse(f.read_text(encoding="utf-8"), c["name"])
        # Neurofounders' analysis text is theirs: keep it local (gitignored), never publish it.
        if info.get("analysis"):
            analyses[c["id"]] = info.pop("analysis")
        c.update(info)
        if n % 25 == 0:
            print(f"[{n}/{len(companies)}] ok", flush=True)

    save_json(DATA / "companies.json", companies)
    save_json(CACHE / "profile_analysis.json", analyses)
    with_site = sum(bool(c.get("website")) for c in companies)
    print(f"Done. website: {with_site}/{len(companies)}; profile errors: {len(missing)}")
    for name, status in missing:
        print(f"  - {name}: HTTP {status}")


if __name__ == "__main__":
    main()
