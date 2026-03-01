from enum import StrEnum


class RunMode(StrEnum):
    BACKTEST = "backtest"
    REPLAY = "replay"
    PAPER = "paper"
    LIVE = "live"


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
