"""Shared Claude helpers for NeuroRadar, with two backends:

- "cli" (default when CLAUDE_CODE_OAUTH_TOKEN is set): runs Claude Code headless
  (`claude -p … --json-schema`) on your Claude subscription. No API billing; it
  uses your plan's usage limits. ANTHROPIC_API_KEY is removed from the child
  environment so nothing can fall back to pay-as-you-go API billing.
- "api" (when only ANTHROPIC_API_KEY is set): Anthropic API, billed per token.

Both use Claude Haiku 5.5 (the lightest model) to keep usage low. A per-run call
cap (CLAUDE_MAX_CALLS, default 350) stops a single run from eating the plan's
limits; whatever is left is picked up by the next run.
"""
import json
import os
import shutil
import subprocess
import threading

from netutil import ROOT

API_MODEL = "claude-haiku-5-5"
CLI_MODEL = "haiku"
MODEL = API_MODEL
# USD per million tokens for claude-haiku-5-5 (API only; Batches are 50% off).
PRICE = {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125}
MAX_CALLS = int(os.environ.get("CLAUDE_MAX_CALLS", "350"))


class CallBudgetExceeded(RuntimeError):
    pass


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


def claude_bin() -> str | None:
    return shutil.which("claude") or (str(p) if (p := os.path.expanduser("~/.local/bin/claude")) and os.path.exists(p) else None)


def backend() -> str | None:
    load_env()
    forced = os.environ.get("CLAUDE_BACKEND")
    if forced in ("cli", "api"):
        return forced
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and claude_bin():
        return "cli"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    return None


def has_key() -> bool:
    return backend() is not None


def can_batch() -> bool:
    return backend() == "api"


def make_client():
    if backend() != "api":
        return None
    import anthropic
    return anthropic.Anthropic(max_retries=4, timeout=300.0)


class Usage:
    """Thread-safe token counter. For the CLI backend the cost is notional
    (covered by the subscription) and only reported for reference."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = self.refusals = 0
        self.tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.batch_tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    def reserve(self):
        with self.lock:
            if self.calls >= MAX_CALLS:
                raise CallBudgetExceeded(f"per-run cap of {MAX_CALLS} Claude calls reached; the rest runs next time")
            self.calls += 1

    def add(self, usage, batch: bool = False):
        bucket = self.batch_tok if batch else self.tok
        get = (lambda k: usage.get(k, 0) or 0) if isinstance(usage, dict) else (lambda k: getattr(usage, k, 0) or 0)
        with self.lock:
            if batch:
                self.calls += 1
            bucket["input"] += get("input_tokens")
            bucket["output"] += get("output_tokens")
            bucket["cache_read"] += get("cache_read_input_tokens")
            bucket["cache_write"] += get("cache_creation_input_tokens")

    def cost(self) -> float:
        std = sum(self.tok[k] * PRICE[k] for k in PRICE) / 1e6
        bat = sum(self.batch_tok[k] * PRICE[k] for k in PRICE) / 1e6 * 0.5
        return round(std + bat, 4)

    def summary(self) -> dict:
        b = backend()
        return {"backend": b, "model": CLI_MODEL if b == "cli" else API_MODEL, "calls": self.calls,
                "refusals": self.refusals, "tokens": self.tok, "batch_tokens": self.batch_tok,
                "api_cost_usd" if b == "api" else "equivalent_api_cost_usd": self.cost()}


USAGE = Usage()


def schema_format(schema: dict) -> dict:
    return {"type": "json_schema", "schema": schema}


def structured(client, system: str, user: str, schema: dict,
               max_tokens: int = 8000, effort: str = "low"):
    """One structured-output call. Returns the parsed dict, or None on refusal."""
    USAGE.reserve()
    if backend() == "cli":
        return _structured_cli(system, user, schema)
    resp = client.messages.create(
        model=API_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": schema_format(schema)},
    )
    USAGE.add(resp.usage)
    return parse_response(resp)


def token_check() -> str:
    """Describe the OAuth token without revealing it (length + prefix check)."""
    raw = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    clean = "".join(raw.split())
    return (f"token length {len(raw)} (without spaces/line breaks: {len(clean)}), "
            f"starts with sk-ant-oat01-: {clean.startswith('sk-ant-oat01-')}")


def _structured_cli(system: str, user: str, schema: dict):
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    # a token copied from a wrapped terminal line often carries line breaks/spaces
    env["CLAUDE_CODE_OAUTH_TOKEN"] = "".join(env.get("CLAUDE_CODE_OAUTH_TOKEN", "").split())
    cmd = [claude_bin(), "-p", "--model", CLI_MODEL, "--output-format", "json",
           "--json-schema", json.dumps(schema), "--system-prompt", system,
           "--tools", "", "--no-session-persistence", "--max-turns", "3"]
    proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=420, env=env)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI: {(proc.stderr or proc.stdout)[:200]}")
    USAGE.add(out.get("usage") or {})
    if out.get("is_error"):
        msg = str(out.get("result", ""))[:200]
        if "usage limit" in msg.lower() or "rate limit" in msg.lower():
            raise CallBudgetExceeded(f"subscription limit reached: {msg}")
        raise RuntimeError(f"claude CLI error: {msg}")
    data = out.get("structured_output")
    if data is None:
        text = (out.get("result") or "").strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(text)
    return data


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
