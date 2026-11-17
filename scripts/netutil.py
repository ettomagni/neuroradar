"""Tiny stdlib HTTP helpers shared by the NeuroRadar scripts."""
import gzip
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (compatible; NeuroRadarBot/0.1; daily neurotech jobs digest)"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"


def fetch(url: str, timeout: float = 20.0, accept: str = "*/*") -> tuple[int, str, str]:
    """Return (status, final_url, text). Never raises for HTTP/network errors."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": accept, "Accept-Encoding": "gzip",
        "Accept-Language": "en,it;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(4_000_000)
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            charset = r.headers.get_content_charset() or "utf-8"
            return r.status, r.geturl(), raw.decode(charset, "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, ""
    except Exception as e:  # DNS, TLS, timeout…
        return 0, url, f"{type(e).__name__}: {e}"


def fetch_json(url: str, timeout: float = 20.0):
    status, _, text = fetch(url, timeout, accept="application/json")
    if status != 200:
        return status, None
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, None


class Throttle:
    """Keep at least `interval` seconds between calls (per instance)."""

    def __init__(self, interval: float):
        self.interval, self.last = interval, 0.0

    def wait(self):
        delta = time.monotonic() - self.last
        if delta < self.interval:
            time.sleep(self.interval - delta)
        self.last = time.monotonic()


def load_companies() -> list[dict]:
    return json.loads((DATA / "companies.json").read_text(encoding="utf-8"))


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
