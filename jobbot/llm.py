"""Thin wrapper around the Claude API: one JSON-schema call, with caching, fallback and cost logging."""
import csv
import json
from datetime import datetime

import anthropic

from .config import API_KEY, MODEL, PRICES, OUTPUT_DIR, FALLBACK_MODELS

_client = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not API_KEY:
            raise SystemExit("No API key: set CLAUDE_API_KEY (or ANTHROPIC_API_KEY) in .env")
        _client = anthropic.Anthropic(api_key=API_KEY)
    return _client


def call_json(*, cached_context: str, instructions: str, user: str, schema: dict,
              effort: str, label: str, model: str = MODEL, max_tokens: int = 32000) -> dict:
    """Send one request and return the parsed JSON object.

    `cached_context` (the profile) goes first in the system prompt with a cache breakpoint, so
    repeated calls in a batch reuse it cheaply (caches are per model).
    """
    extra = {}
    if model in FALLBACK_MODELS:
        # if a safety classifier declines, the API retries on a fallback model
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    with client().beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        **extra,
        system=[
            {"type": "text", "text": cached_context, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": instructions},
        ],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        resp = stream.get_final_message()

    _log_usage(label, resp)

    if resp.stop_reason == "refusal":
        detail = getattr(resp, "stop_details", None)
        raise RuntimeError(f"{label}: model declined the request ({detail})")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(f"{label}: output hit max_tokens={max_tokens}; raise it")

    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def _log_usage(label: str, resp) -> None:
    u = resp.usage
    p_in, p_out, p_cr, p_cw = PRICES.get(resp.model, PRICES["claude-opus-5-5"])
    cache_read = u.cache_read_input_tokens or 0
    cache_write = u.cache_creation_input_tokens or 0
    cost = (u.input_tokens * p_in + u.output_tokens * p_out
            + cache_read * p_cr + cache_write * p_cw) / 1_000_000
    print(f"  [{label}] {resp.model}: in={u.input_tokens} cache_read={cache_read} "
          f"cache_write={cache_write} out={u.output_tokens}  ≈ ${cost:.3f}")
    OUTPUT_DIR.mkdir(exist_ok=True)
    log = OUTPUT_DIR / "usage.csv"
    new = not log.exists()
    with open(log, "a", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "label", "model", "input", "cache_read", "cache_write", "output", "usd"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), label, resp.model, u.input_tokens,
                    cache_read, cache_write, u.output_tokens, f"{cost:.4f}"])
