from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

SAMPLE_SYMBOL = "BTCUSDT"
DEFAULT_OUTPUT_ROOT = Path("data/dev_market_archive/smc_scalper_v1_sample")
_ARCHIVE_START_AT = datetime(2026, 3, 3, 0, 0, tzinfo=UTC)
_RAW_PAYLOAD = json.dumps({"dataset": "smc_scalper_v1_sample"})


def _iso8601(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _price(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _common_fields(*, event_ts: datetime) -> dict[str, Any]:
    return {
        "exchange_name": "bybit",
        "symbol": SAMPLE_SYMBOL,
        "received_at": _iso8601(event_ts + timedelta(milliseconds=50)),
        "raw_payload": _RAW_PAYLOAD,
    }


def _build_kline_row(
    *,
    end_at: datetime,
    interval: str,
    open_price: Decimal,
    high_price: Decimal,
    low_price: Decimal,
    close_price: Decimal,
    volume: Decimal,
) -> dict[str, Any]:
    return {
        **_common_fields(event_ts=end_at),
        "event_type": "kline",
        "event_ts": _iso8601(end_at),
        "interval": interval,
        "start_at": _iso8601(end_at - timedelta(minutes=_interval_minutes(interval))),
        "end_at": _iso8601(end_at),
        "open_price": _price(open_price),
        "high_price": _price(high_price),
        "low_price": _price(low_price),
        "close_price": _price(close_price),
        "volume": format(volume, "f"),
        "turnover": _price(close_price * volume),
        "is_closed": True,
    }


def _interval_minutes(interval: str) -> int:
    if interval == "1m":
        return 1
    if interval == "5m":
        return 5
    if interval == "15m":
        return 15
    raise ValueError(f"unsupported interval: {interval}")


def _build_entry_klines() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    close_price = Decimal("100.00")
    tail = [
        ("104.90", "104.98", "104.84", "104.94"),
        ("104.94", "105.02", "104.88", "104.98"),
        ("104.98", "105.06", "104.92", "105.02"),
        ("105.02", "105.10", "104.96", "105.06"),
        ("105.06", "105.14", "105.00", "105.10"),
        ("105.10", "105.18", "105.04", "105.14"),
        ("105.14", "105.22", "105.08", "105.18"),
        ("105.18", "105.26", "105.12", "105.22"),
        ("105.22", "105.30", "105.16", "105.26"),
        ("105.26", "105.34", "105.20", "105.30"),
        ("105.30", "105.38", "105.24", "105.34"),
        ("105.34", "105.42", "105.28", "105.38"),
        ("105.38", "105.46", "105.32", "105.42"),
        ("105.42", "105.48", "105.28", "105.34"),
        ("105.34", "105.70", "105.32", "105.60"),
        ("105.60", "105.84", "105.58", "105.74"),
        ("105.74", "105.78", "105.62", "105.68"),
        ("105.68", "105.72", "105.56", "105.60"),
        ("105.60", "105.64", "105.50", "105.56"),
        ("105.56", "105.59", "105.46", "105.52"),
        ("105.52", "105.59", "105.22", "105.54"),
        ("105.54", "105.64", "105.48", "105.60"),
        ("105.60", "105.70", "105.54", "105.66"),
        ("105.66", "105.72", "105.56", "105.62"),
        ("105.62", "105.68", "105.54", "105.60"),
    ]

    for index in range(270):
        end_at = _ARCHIVE_START_AT + timedelta(minutes=index + 1)
        if index < 245:
            open_price = close_price
            close_price = close_price + Decimal("0.02")
            high_price = close_price + Decimal("0.06")
            low_price = open_price - Decimal("0.06")
        else:
            open_s, high_s, low_s, close_s = tail[index - 245]
            open_price = Decimal(open_s)
            high_price = Decimal(high_s)
            low_price = Decimal(low_s)
            close_price = Decimal(close_s)
        rows.append(
            _build_kline_row(
                end_at=end_at,
                interval="1m",
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
                volume=Decimal("12"),
            )
        )
    return rows


def _build_structure_klines() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    close_price = Decimal("100.00")
    for index in range(54):
        end_at = _ARCHIVE_START_AT + timedelta(minutes=(index + 1) * 5)
        open_price = close_price
        direction = Decimal("0.08") if index % 7 != 0 else Decimal("-0.03")
        close_price = close_price + direction
        high_price = max(open_price, close_price) + Decimal("0.10")
        low_price = min(open_price, close_price) - Decimal("0.10")
        rows.append(
            _build_kline_row(
                end_at=end_at,
                interval="5m",
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
                volume=Decimal("40"),
            )
        )
    return rows


def _build_bias_klines() -> list[dict[str, Any]]:
    bars = [
        ("100.00", "100.35", "99.85", "100.20"),
        ("100.20", "100.55", "100.05", "100.40"),
        ("100.40", "100.95", "100.20", "100.80"),
        ("100.80", "100.85", "100.35", "100.50"),
        ("100.50", "100.55", "100.00", "100.10"),
        ("100.10", "100.35", "99.95", "100.25"),
        ("100.25", "100.75", "100.10", "100.60"),
        ("100.60", "100.65", "100.20", "100.30"),
        ("100.30", "100.35", "100.10", "100.20"),
        ("100.20", "100.45", "100.05", "100.35"),
        ("100.35", "100.55", "100.25", "100.50"),
        ("100.50", "100.70", "100.40", "100.60"),
        ("100.60", "100.95", "100.55", "100.92"),
        ("100.92", "101.15", "100.80", "101.05"),
        ("101.05", "101.35", "100.95", "101.25"),
        ("101.25", "101.55", "101.10", "101.45"),
        ("101.45", "101.75", "101.30", "101.65"),
        ("101.65", "101.95", "101.50", "101.85"),
    ]
    rows: list[dict[str, Any]] = []
    for index, (open_s, high_s, low_s, close_s) in enumerate(bars):
        end_at = _ARCHIVE_START_AT + timedelta(minutes=(index + 1) * 15)
        rows.append(
            _build_kline_row(
                end_at=end_at,
                interval="15m",
                open_price=Decimal(open_s),
                high_price=Decimal(high_s),
                low_price=Decimal(low_s),
                close_price=Decimal(close_s),
                volume=Decimal("120"),
            )
        )
    return rows


def _build_orderbooks(entry_klines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, kline in enumerate(entry_klines, start=1):
        event_ts = datetime.fromisoformat(kline["event_ts"])
        close_price = Decimal(kline["close_price"])
        rows.append(
            {
                **_common_fields(event_ts=event_ts),
                "event_type": "orderbook",
                "event_ts": kline["event_ts"],
                "depth": 50,
                "sequence": index,
                "update_id": index,
                "is_snapshot": index == 1,
                "bids": [
                    {"price": _price(close_price - Decimal("0.01")), "size": "6"},
                    {"price": _price(close_price - Decimal("0.02")), "size": "4"},
                    {"price": _price(close_price - Decimal("0.03")), "size": "2"},
                ],
                "asks": [
                    {"price": _price(close_price + Decimal("0.01")), "size": "2"},
                    {"price": _price(close_price + Decimal("0.02")), "size": "2"},
                    {"price": _price(close_price + Decimal("0.03")), "size": "1"},
                ],
            }
        )
    return rows


def _build_open_interest(entry_klines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    value = Decimal("100000")
    for kline in entry_klines:
        event_ts = datetime.fromisoformat(kline["event_ts"])
        value += Decimal("50")
        rows.append(
            {
                **_common_fields(event_ts=event_ts),
                "event_type": "open_interest",
                "event_ts": kline["event_ts"],
                "open_interest": format(value, "f"),
                "interval": "5m",
            }
        )
    return rows


def _build_funding_rows() -> list[dict[str, Any]]:
    event_ts = _ARCHIVE_START_AT + timedelta(minutes=5)
    next_funding_at = _ARCHIVE_START_AT + timedelta(hours=8)
    return [
        {
            **_common_fields(event_ts=event_ts),
            "event_type": "funding_rate",
            "event_ts": _iso8601(event_ts),
            "funding_rate": "0.0001",
            "next_funding_at": _iso8601(next_funding_at),
        }
    ]


def _write_rows(root: Path, *, event_type: str, rows: list[dict[str, Any]]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("pyarrow dependency is required to build sample archives") from exc

    partitions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        event_ts = datetime.fromisoformat(row["event_ts"])
        key = (event_ts.strftime("%Y-%m-%d"), event_ts.strftime("%H"))
        partitions.setdefault(key, []).append(row)

    for (date_part, hour_part), part_rows in partitions.items():
        path = root / f"exchange=bybit/event_type={event_type}/date={date_part}/hour={hour_part}/symbol={SAMPLE_SYMBOL}"
        path.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(part_rows), path / "part-00000.parquet")


def build_smc_sample_archive(output_root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    if output_root.exists() and any(output_root.rglob("*.parquet")):
        raise FileExistsError(f"output archive already exists: {output_root}")

    entry_klines = _build_entry_klines()
    _write_rows(output_root, event_type="kline", rows=entry_klines + _build_structure_klines() + _build_bias_klines())
    _write_rows(output_root, event_type="orderbook", rows=_build_orderbooks(entry_klines))
    _write_rows(output_root, event_type="open_interest", rows=_build_open_interest(entry_klines))
    _write_rows(output_root, event_type="funding_rate", rows=_build_funding_rows())
    return output_root


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a replay archive that opens and closes one SMC long setup.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT, help="Destination archive root.")
    args = parser.parse_args()

    output_root = build_smc_sample_archive(args.output)
    summary = {
        "output_root": str(output_root),
        "strategy": "smc_scalper_v1",
        "symbol": SAMPLE_SYMBOL,
        "start_at": _iso8601(_ARCHIVE_START_AT + timedelta(minutes=1)),
        "end_at": _iso8601(_ARCHIVE_START_AT + timedelta(minutes=270)),
        "expected_signals": ["open_long", "close_long"],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
