from __future__ import annotations

from collections.abc import Sequence

from trading_bot.config.schema import MarketDataConfig
from trading_bot.timeframes import canonicalize_interval


def build_public_topics(symbols: Sequence[str], market_data: MarketDataConfig) -> list[str]:
    topics: list[str] = []
    for symbol in symbols:
        symbol_lower = symbol.lower()
        topics.append(f"{symbol_lower}@depth@100ms")
        for interval in market_data.kline_intervals:
            topics.append(f"{symbol_lower}@kline_{canonicalize_interval(interval)}")
        if market_data.enable_trades:
            topics.append(f"{symbol_lower}@trade")
        if market_data.enable_ticker:
            topics.append(f"{symbol_lower}@bookTicker")
        if market_data.enable_funding:
            topics.append(f"{symbol_lower}@markPrice@1s")
        if market_data.enable_liquidations:
            topics.append(f"{symbol_lower}@forceOrder")
    return topics
