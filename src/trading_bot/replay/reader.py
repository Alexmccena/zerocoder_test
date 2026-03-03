from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from trading_bot.marketdata.events import (
    FundingRateEvent,
    KlineEvent,
    LiquidationEvent,
    MarketEvent,
    OpenInterestEvent,
    OrderBookEvent,
    TickerEvent,
    TradeEvent,
)


EVENT_TYPE_PRIORITY = {
    "orderbook": 0,
    "trade": 1,
    "ticker": 2,
    "kline": 3,
    "open_interest": 4,
    "funding_rate": 5,
    "liquidation": 6,
}

EVENT_TYPE_MODEL = {
    "orderbook": OrderBookEvent,
    "trade": TradeEvent,
    "ticker": TickerEvent,
    "kline": KlineEvent,
    "open_interest": OpenInterestEvent,
    "funding_rate": FundingRateEvent,
    "liquidation": LiquidationEvent,
}


@dataclass(frozen=True, slots=True)
class _ReplayRow:
    event: MarketEvent
    file_path: str
    row_index: int


class ReplayReader:
    def __init__(
        self,
        *,
        source_root: Path,
        start_at: datetime | None,
        end_at: datetime | None,
        fail_on_gap: bool,
        max_gap_seconds: int,
    ) -> None:
        self.source_root = source_root
        self.start_at = start_at
        self.end_at = end_at
        self.fail_on_gap = fail_on_gap
        self.max_gap_seconds = max_gap_seconds

    def read_events(self, *, symbols: list[str]) -> list[MarketEvent]:
        rows = self._load_rows(symbols=symbols)
        rows.sort(
            key=lambda item: (
                item.event.event_ts,
                item.event.received_at,
                EVENT_TYPE_PRIORITY[item.event.event_type],
                item.file_path,
                item.row_index,
            )
        )
        self._validate_rows(rows)
        return [row.event for row in rows]

    def _load_rows(self, *, symbols: list[str]) -> list[_ReplayRow]:
        try:
            import pyarrow.parquet as pq
        except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("pyarrow dependency is required for replay/backtest") from exc

        symbol_set = set(symbols)
        rows: list[_ReplayRow] = []
        for path in sorted(self.source_root.rglob("*.parquet")):
            if not self._matches_symbol(path=path, symbols=symbol_set):
                continue
            table = pq.ParquetFile(path).read()
            for index, row in enumerate(table.to_pylist()):
                normalized = self._normalize_row(row)
                event_type = normalized.get("event_type")
                model = EVENT_TYPE_MODEL.get(event_type)
                if model is None:
                    continue
                event = model.model_validate(normalized)
                if self.start_at is not None and event.event_ts < self.start_at:
                    continue
                if self.end_at is not None and event.event_ts > self.end_at:
                    continue
                rows.append(_ReplayRow(event=event, file_path=str(path), row_index=index))
        return rows

    def _matches_symbol(self, *, path: Path, symbols: set[str]) -> bool:
        symbol_part = next((part for part in path.parts if part.startswith("symbol=")), None)
        if symbol_part is None:
            return False
        return symbol_part.split("=", 1)[1] in symbols

    def _normalize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        if isinstance(normalized.get("raw_payload"), str):
            normalized["raw_payload"] = json.loads(normalized["raw_payload"])
        return normalized

    def _validate_rows(self, rows: list[_ReplayRow]) -> None:
        previous_ts: datetime | None = None
        for row in rows:
            if row.event.event_ts is None:
                raise RuntimeError("replay dataset contains event without event_ts")
            if previous_ts is not None and row.event.event_ts < previous_ts:
                raise RuntimeError("replay dataset has timestamp regression")
            if (
                self.fail_on_gap
                and previous_ts is not None
                and (row.event.event_ts - previous_ts).total_seconds() > self.max_gap_seconds
            ):
                raise RuntimeError("replay dataset gap exceeded configured max_gap_seconds")
            previous_ts = row.event.event_ts
