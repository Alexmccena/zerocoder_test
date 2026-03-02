from __future__ import annotations

from collections.abc import Sequence

from trading_bot.config.schema import MarketDataConfig


def build_public_topics(symbols: Sequence[str], market_data: MarketDataConfig) -> list[str]:
    topics: list[str] = []
    for symbol in symbols:
        topics.append(f"orderbook.{market_data.orderbook_depth}.{symbol}")
        if market_data.enable_trades:
            topics.append(f"publicTrade.{symbol}")
        if market_data.enable_ticker:
            topics.append(f"tickers.{symbol}")
        if market_data.enable_liquidations:
            topics.append(f"allLiquidation.{symbol}")
        for interval in market_data.kline_intervals:
            topics.append(f"kline.{interval}.{symbol}")
    return topics


def build_private_topics() -> list[str]:
    return ["wallet", "order", "execution", "position"]
