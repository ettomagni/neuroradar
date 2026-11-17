"""Build data/companies.json from section 8 of docs/PROMPT.md.

Cities are not known yet (Neurofounders only lists the country), so each pin is
placed around its country's centroid on a deterministic sunflower spiral and
marked location_precision="country". The enrichment phase will replace these
with real HQ coordinates.

Usage: python3 scripts/build_seed.py
"""
import json
import math
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "docs" / "PROMPT.md"
OUT = ROOT / "data" / "companies.json"

CATEGORIES = {
    "Brain-Computer Interface": "bci",
    "Consumer Neurotech": "consumer",
    "Diagnostics and Assessment": "diagnostics",
    "Neuroimaging": "neuroimaging",
    "Neuromodulation": "neuromodulation",
    "Tools and Infrastructure": "tools",
}

# country -> (lat, lng, max spread radius in degrees)
COUNTRIES = {
    "USA": (39.0, -96.0, 9.0),
    "Canada": (47.5, -77.0, 4.0),
    "UK": (52.6, -1.6, 1.6),
    "Ireland": (53.3, -7.8, 0.8),
    "France": (46.6, 2.4, 2.2),
    "Germany": (51.0, 10.2, 2.0),
    "Switzerland": (46.8, 8.2, 0.6),
    "Netherlands": (52.2, 5.4, 0.6),
    "Belgium": (50.7, 4.6, 0.5),
    "Spain": (40.3, -3.7, 2.2),
    "Italy": (43.0, 12.5, 1.8),
    "Austria": (47.6, 14.6, 0.8),
    "Denmark": (55.9, 10.0, 0.7),
    "Sweden": (59.5, 15.5, 2.0),
    "Norway": (60.5, 9.0, 1.5),
    "Finland": (62.0, 25.5, 1.8),
    "Poland": (52.0, 19.4, 1.5),
    "Czechia": (49.8, 15.5, 0.8),
    "Hungary": (47.2, 19.4, 0.8),
    "Slovenia": (46.1, 14.8, 0.4),
    "Lithuania": (55.3, 23.9, 0.6),
    "Turkey": (39.0, 33.0, 2.0),
    "Israel": (31.8, 34.9, 0.5),
    "India": (21.0, 78.0, 4.0),
    "China": (32.0, 112.0, 5.0),
    "Hong Kong": (22.33, 114.15, 0.1),
    "Taiwan": (23.7, 121.0, 0.6),
    "South Korea": (36.4, 127.9, 0.8),
    "Japan": (36.2, 138.3, 2.0),
    "Singapore": (1.35, 103.82, 0.08),
    "Australia": (-30.0, 145.0, 5.0),
    "New Zealand": (-41.0, 174.0, 1.2),
    "Argentina": (-34.6, -60.0, 2.5),
}

ACQUIRED = {"CTRL-labs (Meta)": "Meta", "NextMind (Snap AR)": "Snap"}
SLUG_OVERRIDES = {
    "Arbor Neurotech": "forest-neurotech",
    "eNeura": "eneura-edb82",
    "Fluent": "fluent-bci",
    "NextMind (Snap AR)": "nextmind-snap-ar",
}

LINE = re.compile(r"^- \*\*(.+?)\*\* · (.+?) · (.+)$")
HEAD = re.compile(r"^### \S+ (.+?) \((\d+)\)$")


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"\(.*?\)", "", s).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def main() -> None:
    text = PROMPT.read_text(encoding="utf-8")
    section = text.split("## 8.", 1)[1]
    companies, category, expected = [], None, {}
    for raw in section.splitlines():
        line = raw.strip()
        if m := HEAD.match(line):
            category = CATEGORIES[m.group(1)]
            expected[category] = int(m.group(2))
        elif (m := LINE.match(line)) and category:
            name, country, desc = m.groups()
            if country not in COUNTRIES:
                raise SystemExit(f"Unknown country {country!r} for {name}")
            companies.append({
                "id": slugify(name),
                "name": name,
                "category": category,
                "country": country,
                "description": desc,
                "neurofounders_slug": SLUG_OVERRIDES.get(name, slugify(name)),
                "status": "acquired" if name in ACQUIRED else "active",
                "parent": ACQUIRED.get(name),
                "website": None,
                "careers_url": None,
                "ats": None,
                "city": None,
            })

    for cat, n in expected.items():
        got = sum(c["category"] == cat for c in companies)
        assert got == n, f"{cat}: expected {n}, parsed {got}"

    # Deterministic sunflower spread around each country's centroid.
    by_country: dict[str, list] = {}
    for c in sorted(companies, key=lambda c: c["name"].lower()):
        by_country.setdefault(c["country"], []).append(c)
    golden = math.radians(137.508)
    for country, items in by_country.items():
        lat0, lng0, rmax = COUNTRIES[country]
        n = len(items)
        for i, c in enumerate(items):
            r = 0.0 if n == 1 else rmax * math.sqrt((i + 0.5) / n)
            th = i * golden
            c["lat"] = round(lat0 + r * math.sin(th), 4)
            c["lng"] = round(lng0 + r * math.cos(th) / math.cos(math.radians(lat0)), 4)
            c["location_precision"] = "country"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(companies, ensure_ascii=False, indent=1), encoding="utf-8")
    ids = [c["id"] for c in companies]
    assert len(ids) == len(set(ids)), "duplicate ids"
    print(f"Wrote {len(companies)} companies to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
