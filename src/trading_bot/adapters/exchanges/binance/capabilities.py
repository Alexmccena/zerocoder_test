from __future__ import annotations

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExchangeName
from trading_bot.domain.models import ExchangeCapabilities


def build_binance_capabilities(config: AppSettings) -> ExchangeCapabilities:
    private_enabled = config.exchange.private_state_enabled
    return ExchangeCapabilities(
        exchange_name=ExchangeName.BINANCE,
        channels={
            "orderbook": True,
            "trade": config.market_data.enable_trades,
            "ticker": config.market_data.enable_ticker,
            "kline": True,
            "liquidation": config.market_data.enable_liquidations,
            "open_interest": config.market_data.enable_open_interest,
            "funding_rate": config.market_data.enable_funding,
            "private_wallet": private_enabled,
            "private_order": private_enabled,
            "private_execution": private_enabled,
            "private_position": private_enabled,
        },
        rest_features={
            "instruments": True,
            "recent_klines": True,
            "open_interest": True,
            "funding_rate": True,
            "private_wallet": private_enabled,
            "private_positions": private_enabled,
            "private_open_orders": private_enabled,
            "order_create": private_enabled,
            "order_cancel": private_enabled,
            "order_query": private_enabled,
        },
    )
