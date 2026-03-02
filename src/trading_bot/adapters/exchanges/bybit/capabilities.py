from __future__ import annotations

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExchangeName
from trading_bot.domain.models import ExchangeCapabilities


def build_bybit_capabilities(config: AppSettings) -> ExchangeCapabilities:
    return ExchangeCapabilities(
        exchange_name=ExchangeName.BYBIT,
        channels={
            "orderbook": True,
            "trade": config.market_data.enable_trades,
            "ticker": config.market_data.enable_ticker,
            "liquidation": config.market_data.enable_liquidations,
            "private_wallet": config.exchange.private_state_enabled,
            "private_order": config.exchange.private_state_enabled,
            "private_execution": config.exchange.private_state_enabled,
            "private_position": config.exchange.private_state_enabled,
        },
        rest_features={
            "instrument_info": True,
            "open_interest": config.market_data.enable_open_interest,
            "funding_rate": config.market_data.enable_funding,
            "private_wallet": config.exchange.private_state_enabled,
            "private_positions": config.exchange.private_state_enabled,
            "private_open_orders": config.exchange.private_state_enabled,
        },
    )
