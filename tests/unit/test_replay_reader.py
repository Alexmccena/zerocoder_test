from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from trading_bot.replay.reader import ReplayReader


def _write_parquet(root: Path, *, event_type: str, symbol: str, rows: list[dict]) -> None:
    path = root / f"exchange=bybit/event_type={event_type}/date=2026-03-03/hour=00/symbol={symbol}"
    path.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path / "part-00000.parquet")


def test_replay_reader_sorts_by_priority_and_timestamp(tmp_path: Path) -> None:
    event_ts = datetime(2026, 3, 3, tzinfo=timezone.utc)
    base_row = {
        "exchange_name": "bybit",
        "symbol": "BTCUSDT",
        "event_ts": event_ts.isoformat(),
        "received_at": event_ts.isoformat(),
        "raw_payload": json.dumps({}),
    }
    _write_parquet(
        tmp_path,
        event_type="trade",
        symbol="BTCUSDT",
        rows=[
            {
                **base_row,
                "event_type": "trade",
                "trade_id": "t1",
                "side": "Buy",
                "price": "100",
                "quantity": "1",
            }
        ],
    )
    _write_parquet(
        tmp_path,
        event_type="orderbook",
        symbol="BTCUSDT",
        rows=[
            {
                **base_row,
                "event_type": "orderbook",
                "depth": 50,
                "sequence": 1,
                "update_id": 1,
                "is_snapshot": True,
                "bids": [{"price": "99", "size": "1"}],
                "asks": [{"price": "100", "size": "1"}],
            }
        ],
    )

    reader = ReplayReader(
        source_root=tmp_path,
        start_at=None,
        end_at=None,
        fail_on_gap=True,
        max_gap_seconds=30,
    )

    events = reader.read_events(symbols=["BTCUSDT"])

    assert [event.event_type for event in events] == ["orderbook", "trade"]
