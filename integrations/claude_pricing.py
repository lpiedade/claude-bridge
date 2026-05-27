"""Compute USD cost from Claude transcript ``usage`` dicts.

Newer Claude CLI transcripts no longer carry per-row ``costUSD`` or
``total_cost_usd`` — they only emit ``usage`` with token counts. This module
maps a model id to its public per-million-token rates and computes cost from a
single ``usage`` dict or an iterable of them.

Rates are Anthropic public list prices (USD per million tokens) for the
Claude 4.x family. Update when new families ship.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ModelRates:
    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float


# Per-million-token rates, USD. Keys are matched as substrings (lowercased)
# against the model id, longest-first.
MODEL_RATES: dict[str, ModelRates] = {
    "opus-4": ModelRates(
        input=15.0, output=75.0,
        cache_write_5m=18.75, cache_write_1h=30.0, cache_read=1.50,
    ),
    "sonnet-4": ModelRates(
        input=3.0, output=15.0,
        cache_write_5m=3.75, cache_write_1h=6.0, cache_read=0.30,
    ),
    "haiku-4": ModelRates(
        input=1.0, output=5.0,
        cache_write_5m=1.25, cache_write_1h=2.0, cache_read=0.10,
    ),
}

DEFAULT_RATES = MODEL_RATES["haiku-4"]


def rates_for(model: str | None) -> ModelRates:
    if not model:
        return DEFAULT_RATES
    m = model.lower()
    for key in sorted(MODEL_RATES, key=len, reverse=True):
        if key in m:
            return MODEL_RATES[key]
    return DEFAULT_RATES


def cost_from_usage(usage: dict, model: str | None) -> float:
    """Compute cost in USD from one ``usage`` dict.

    Splits cache-creation tokens between 5m and 1h tiers when the nested
    ``cache_creation`` breakdown is present; otherwise charges all of them at
    the 5m rate (the cheaper default and the most common in practice).
    """
    if not isinstance(usage, dict):
        return 0.0
    r = rates_for(model)
    inp = _f(usage.get("input_tokens"))
    out = _f(usage.get("output_tokens"))
    cache_read = _f(usage.get("cache_read_input_tokens"))
    cache_create_total = _f(usage.get("cache_creation_input_tokens"))

    breakdown = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else None
    if breakdown:
        cw_5m = _f(breakdown.get("ephemeral_5m_input_tokens"))
        cw_1h = _f(breakdown.get("ephemeral_1h_input_tokens"))
    else:
        cw_5m, cw_1h = cache_create_total, 0.0

    return (
        inp * r.input
        + out * r.output
        + cache_read * r.cache_read
        + cw_5m * r.cache_write_5m
        + cw_1h * r.cache_write_1h
    ) / 1_000_000


def cost_from_usages(usages: Iterable[tuple[dict, str | None]]) -> float:
    return sum(cost_from_usage(u, m) for u, m in usages)


def _f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
