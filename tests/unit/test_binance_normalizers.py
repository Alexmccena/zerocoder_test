from __future__ import annotations

from decimal import Decimal

from trading_bot.adapters.exchanges.binance.normalizers import normalize_private_message
from trading_bot.marketdata.events import WalletEvent


def test_account_update_uses_position_unrealized_pnl_not_reason_code() -> None:
    message = {
        "e": "ACCOUNT_UPDATE",
        "E": 1772785000000,
        "a": {
            "m": "ORDER",
            "B": [{"a": "USDT", "wb": "25.200", "cw": "20.620", "bc": "0.010"}],
            "P": [
                {"s": "ETHUSDT", "up": "0.120", "pa": "0.022", "ep": "2080.0", "ps": "BOTH"},
                {"s": "BTCUSDT", "up": "-0.020", "pa": "0", "ep": "0", "ps": "BOTH"},
            ],
        },
    }

    events = normalize_private_message(message)
    wallet = next(event for event in events if isinstance(event, WalletEvent))

    assert wallet.wallet_balance == Decimal("25.200")
    assert wallet.available_balance == Decimal("20.620")
    assert wallet.unrealized_pnl == Decimal("0.100")
