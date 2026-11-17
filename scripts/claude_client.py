"""Shared Claude API helpers for NeuroRadar.

- Reads ANTHROPIC_API_KEY from the environment (GitHub secret) or from a local
  .env file (gitignored).
- `structured()` returns schema-validated JSON (structured outputs) and opts into
  server-side refusal fallbacks.
- Tracks token usage and an estimated cost for the run log.
"""
import json
import os
import threading

import anthropic

from netutil import ROOT

MODEL = "claude-opus-5-5"
# USD per million tokens for claude-opus-5-5 (standard API; Batches are 50% off).
PRICE = {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if value and not os.environ.get(key):
            os.environ[key] = value


def has_key() -> bool:
    load_env()
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def make_client() -> anthropic.Anthropic:
    load_env()
    return anthropic.Anthropic(max_retries=4, timeout=300.0)


class Usage:
    """Thread-safe token counter with a cost estimate."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = self.refusals = 0
        self.tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.batch_tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    def add(self, usage, batch: bool = False):
        bucket = self.batch_tok if batch else self.tok
        with self.lock:
            self.calls += 1
            bucket["input"] += usage.input_tokens or 0
            bucket["output"] += usage.output_tokens or 0
            bucket["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
            bucket["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def cost(self) -> float:
        std = sum(self.tok[k] * PRICE[k] for k in PRICE) / 1e6
        bat = sum(self.batch_tok[k] * PRICE[k] for k in PRICE) / 1e6 * 0.5
        return round(std + bat, 4)

    def summary(self) -> dict:
        return {"model": MODEL, "calls": self.calls, "refusals": self.refusals,
                "tokens": self.tok, "batch_tokens": self.batch_tok, "est_cost_usd": self.cost()}


USAGE = Usage()


def schema_format(schema: dict) -> dict:
    return {"type": "json_schema", "schema": schema}


def structured(client, system: str, user: str, schema: dict,
               max_tokens: int = 8000, effort: str = "low"):
    """One structured-output call. Returns the parsed dict, or None on refusal."""
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": schema_format(schema)},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    USAGE.add(resp.usage)
    return parse_response(resp)


def parse_response(resp):
    if resp.stop_reason == "refusal":
        USAGE.refusals += 1
        return None
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("response truncated (max_tokens)")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"no text block (stop_reason={resp.stop_reason})")
    return json.loads(text)
