from __future__ import annotations

from typing import Any


_ALIASES = {
    "1": "1m",
    "1m": "1m",
    "1min": "1m",
    "5": "5m",
    "5m": "5m",
    "5min": "5m",
    "15": "15m",
    "15m": "15m",
    "15min": "15m",
}


def canonicalize_interval(value: Any) -> str:
    text = str(value).strip().lower()
    if text in _ALIASES:
        return _ALIASES[text]
    if text.endswith("m") and text[:-1].isdigit():
        return f"{int(text[:-1])}m"
    if text.isdigit():
        return f"{int(text)}m"
    raise ValueError(f"Unsupported kline interval: {value!r}")


def interval_to_minutes(value: Any) -> int:
    interval = canonicalize_interval(value)
    return int(interval.removesuffix("m"))


def interval_to_bybit(value: Any) -> str:
    return str(interval_to_minutes(value))
