"""Best-effort per-request LLM cost estimates for captured conversations."""
from __future__ import annotations

import json
from typing import Any, Iterable

from memory_common.context_compression import estimate_tokens


# Conservative fallback prices in USD per one million tokens. Deployments can
# override or extend these through MEMORY_MODEL_PRICES_JSON without a code change.
MODEL_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "gpt-5.4-pro": (30.00, 0.00, 180.00),
    "gpt-5.2": (1.75, 0.175, 14.00),
}
DEFAULT_MODEL = "gpt-5.4"


def model_prices() -> dict[str, tuple[float, float, float]]:
    from memory_common.config import get_settings
    prices = dict(MODEL_PRICES)
    try:
        configured = json.loads(get_settings().memory_model_prices_json or "{}")
        for name, value in configured.items():
            if isinstance(value, (list, tuple)) and len(value) == 3:
                prices[str(name).casefold()] = tuple(float(item) for item in value)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return prices


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _model(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("model") or metadata.get("model_name")
    return str(value).strip().lower() if value else None


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    return next((mapping[key] for key in keys if mapping.get(key) is not None), None)


def _price_key(model: str | None) -> str:
    prices = model_prices()
    if model in prices:
        return model
    for key in sorted(prices, key=len, reverse=True):
        if model and model.startswith(key + "-"):
            return key
    return DEFAULT_MODEL


def format_usd(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 0.01:
        return f"${value:.5f}".rstrip("0").rstrip(".")
    return f"${value:.3f}".rstrip("0").rstrip(".")


def estimate_request_cost(messages: Iterable[Any]) -> dict[str, Any]:
    """Estimate one user request, preferring captured provider usage when present.

    Without usage, each assistant message is treated as one model invocation:
    its input is all captured context before it and its output is that message.
    This accounts for tool-result round trips while remaining tokenizer-free.
    """
    ordered = list(messages)
    metadata = [getattr(item, "metadata_", None) or {} for item in ordered]
    model = next((_model(item) for item in reversed(metadata) if _model(item)), None)
    usage = next((item.get("usage") for item in reversed(metadata) if isinstance(item.get("usage"), dict)), None)

    exact_input = _integer(_first(usage or {}, "input_tokens", "prompt_tokens"))
    exact_output = _integer(_first(usage or {}, "output_tokens", "completion_tokens"))
    cached = _integer(
        (usage or {}).get("cached_input_tokens")
        or ((usage or {}).get("input_tokens_details") or {}).get("cached_tokens")
        or ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
    ) or 0

    if exact_input is not None and exact_output is not None:
        input_tokens, output_tokens, accuracy = exact_input, exact_output, "usage"
    else:
        context: list[str] = []
        input_tokens = output_tokens = 0
        invocations = 0
        for item in ordered:
            content = getattr(item, "content", "") or ""
            if getattr(item, "role", "") == "assistant":
                input_tokens += sum(estimate_tokens(text) for text in context)
                output_tokens += estimate_tokens(content)
                invocations += 1
            context.append(content)
        if not invocations:  # an active request with no response yet
            input_tokens = sum(estimate_tokens(text) for text in context)
        accuracy = "estimated"

    price_model = _price_key(model)
    prices = model_prices()
    input_rate, cached_rate, output_rate = prices.get(price_model, prices[DEFAULT_MODEL])
    uncached = max(0, input_tokens - cached)
    cost = (uncached * input_rate + cached * cached_rate + output_tokens * output_rate) / 1_000_000
    return {
        "model": model or price_model,
        "price_model": price_model,
        "model_assumed": model is None or price_model != model,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "cost_label": format_usd(cost),
        "accuracy": accuracy,
    }
