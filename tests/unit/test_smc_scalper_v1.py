from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.config.schema import AppSettings
from trading_bot.domain.enums import ExchangeName, ExecutionVenueKind, MarketType, TradeAction
from trading_bot.domain.models import (
    BiasState,
    FairValueGapZone,
    FeatureSnapshot,
    FundingFeatureState,
    LiquiditySweepState,
    MarketSnapshot,
    OpenInterestFeatureState,
    OrderBookFeatureState,
    PositionState,
    RuntimeState,
)
from trading_bot.marketdata.events import KlineEvent, OrderBookEvent, OrderBookLevel, TickerEvent
from trading_bot.strategies.smc_scalper_v1 import SmcScalperV1Strategy


def _build_settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "runtime": {"service_name": "tb", "mode": "paper", "environment": "dev"},
            "exchange": {
                "primary": "bybit",
                "market_type": "linear_perp",
                "position_mode": "one_way",
                "account_alias": "default",
                "testnet": True,
            },
            "symbols": {"allowlist": ["BTCUSDT"]},
            "storage": {"postgres_dsn": "postgresql+asyncpg://u:p@localhost/db", "redis_dsn": "redis://localhost:6379/0"},
            "observability": {"log_level": "INFO", "http_host": "127.0.0.1", "http_port": 8080},
            "strategy": {
                "name": "smc_scalper_v1",
                "smc_scalper_v1": {
                        "history": {
                            "entry_bars": 16,
                            "structure_bars": 16,
                            "bias_bars": 16,
                        "orderbook_snapshots": 4,
                        "oi_points": 4,
                        "liquidation_events": 8,
                    }
                },
            },
            "risk": {"max_open_positions": 2, "risk_per_trade": 0.1, "max_daily_loss": 0.2},
            "llm": {"enabled": False, "provider": "none", "model_name": "", "timeout_seconds": 10},
        }
    )


def _build_snapshot() -> MarketSnapshot:
    now = datetime.now(timezone.utc)
    return MarketSnapshot(
        symbol="BTCUSDT",
        as_of=now,
        instrument={
            "exchange_name": ExchangeName.BYBIT,
            "symbol": "BTCUSDT",
            "market_type": MarketType.LINEAR_PERP,
            "tick_size": Decimal("0.1"),
            "lot_size": Decimal("0.001"),
            "min_quantity": Decimal("0.001"),
            "quote_asset": "USDT",
            "base_asset": "BTC",
        },
        orderbook=OrderBookEvent(
            exchange_name=ExchangeName.BYBIT,
            symbol="BTCUSDT",
            event_ts=now,
            depth=50,
            bids=[OrderBookLevel(price=Decimal("100"), size=Decimal("5"))],
            asks=[OrderBookLevel(price=Decimal("100.1"), size=Decimal("4"))],
        ),
        ticker=TickerEvent(
            exchange_name=ExchangeName.BYBIT,
            symbol="BTCUSDT",
            event_ts=now,
            last_price=Decimal("100.05"),
        ),
        closed_klines_by_interval={
            "1m": KlineEvent(
                exchange_name=ExchangeName.BYBIT,
                symbol="BTCUSDT",
                event_ts=now,
                interval="1m",
                start_at=now,
                end_at=now,
                open_price=Decimal("100"),
                high_price=Decimal("100.3"),
                low_price=Decimal("99.8"),
                close_price=Decimal("100.05"),
                volume=Decimal("10"),
                is_closed=True,
            )
        },
    )


def _build_long_features() -> FeatureSnapshot:
    now = datetime.now(timezone.utc)
    return FeatureSnapshot(
        symbol="BTCUSDT",
        warmup_complete=True,
        has_fresh_orderbook=True,
        bias_state=BiasState(timeframe="15m", state="bullish", direction="bullish"),
        sweep=LiquiditySweepState(
            side="long",
            swept_level=Decimal("99.7"),
            sweep_at=now,
            reclaim_at=now,
            age_bars=1,
            is_active=True,
        ),
        active_fvgs=[
            FairValueGapZone(
                side="long",
                lower_bound=Decimal("99.9"),
                upper_bound=Decimal("100.2"),
                created_at=now,
                age_bars=1,
                touched=True,
            )
        ],
        orderbook_state=OrderBookFeatureState(
            has_fresh_orderbook=True,
            supportive_long_imbalance=True,
            has_bid_wall=True,
        ),
        open_interest_state=OpenInterestFeatureState(
            available=True,
            delta_bps=Decimal("8"),
            supportive_long=True,
        ),
        funding_state=FundingFeatureState(
            enabled=True,
            available=True,
            funding_rate=Decimal("0.0001"),
            blocks_long=False,
        ),
    )


def _build_short_features() -> FeatureSnapshot:
    now = datetime.now(timezone.utc)
    return FeatureSnapshot(
        symbol="BTCUSDT",
        warmup_complete=True,
        has_fresh_orderbook=True,
        bias_state=BiasState(timeframe="15m", state="bearish", direction="bearish"),
        sweep=LiquiditySweepState(
            side="short",
            swept_level=Decimal("100.4"),
            sweep_at=now,
            reclaim_at=now,
            age_bars=1,
            is_active=True,
        ),
        active_fvgs=[
            FairValueGapZone(
                side="short",
                lower_bound=Decimal("99.9"),
                upper_bound=Decimal("100.2"),
                created_at=now,
                age_bars=1,
                touched=True,
            )
        ],
        orderbook_state=OrderBookFeatureState(
            has_fresh_orderbook=True,
            supportive_short_imbalance=True,
            has_ask_wall=True,
        ),
        open_interest_state=OpenInterestFeatureState(
            available=True,
            delta_bps=Decimal("-8"),
            supportive_short=True,
        ),
        funding_state=FundingFeatureState(
            enabled=True,
            available=True,
            funding_rate=Decimal("-0.0001"),
            blocks_short=False,
        ),
    )


async def test_smc_scalper_opens_long_setup() -> None:
    settings = _build_settings()
    state = RuntimeState(run_session_id="run-1", run_mode=settings.runtime.mode, execution_venue=ExecutionVenueKind.PAPER)
    strategy = SmcScalperV1Strategy(config=settings, runtime_state_provider=lambda: state)

    intents = await strategy.evaluate(_build_snapshot(), _build_long_features())

    assert intents[0].action == TradeAction.OPEN_LONG
    assert intents[0].metadata["zone_type"] == "fvg"
    assert intents[0].metadata["selected_setup"]["side"] == "long"


async def test_smc_scalper_closes_on_opposite_setup() -> None:
    settings = _build_settings()
    state = RuntimeState(run_session_id="run-1", run_mode=settings.runtime.mode, execution_venue=ExecutionVenueKind.PAPER)
    strategy = SmcScalperV1Strategy(config=settings, runtime_state_provider=lambda: state)
    snapshot = _build_snapshot()

    open_intent = (await strategy.evaluate(snapshot, _build_long_features()))[0]
    assert open_intent.action == TradeAction.OPEN_LONG

    state.open_positions["BTCUSDT"] = PositionState(
        exchange_name=ExchangeName.BYBIT,
        execution_venue=ExecutionVenueKind.PAPER,
        symbol="BTCUSDT",
        side="long",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
    )

    close_intent = (await strategy.evaluate(snapshot, _build_short_features()))[0]

    assert close_intent.action == TradeAction.CLOSE_LONG
    assert close_intent.metadata["close_reason"] == "opposite_setup"
