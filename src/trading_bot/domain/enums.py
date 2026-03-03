from enum import StrEnum


class RunMode(StrEnum):
    BACKTEST = "backtest"
    REPLAY = "replay"
    PAPER = "paper"
    LIVE = "live"
    CAPTURE = "capture"


class Environment(StrEnum):
    DEV = "dev"
    PROD = "prod"
    TEST = "test"


class ExchangeName(StrEnum):
    BYBIT = "bybit"
    BINANCE = "binance"
    MEXC = "mexc"


class MarketType(StrEnum):
    LINEAR_PERP = "linear_perp"
    SPOT = "spot"


class PositionMode(StrEnum):
    ONE_WAY = "one_way"
    HEDGE = "hedge"


class ServiceStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class ExecutionVenueKind(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class TradeAction(StrEnum):
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"


class EntryType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class RiskDecisionType(StrEnum):
    ALLOW = "allow"
    REJECT = "reject"
    HALT = "halt"
