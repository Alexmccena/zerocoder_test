from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from trading_bot.marketdata.events import MarketEvent
from trading_bot.observability.metrics import AppMetrics


class ParquetArchiveWriter:
    def __init__(
        self,
        *,
        root: Path,
        compression: str,
        flush_rows: int,
        flush_seconds: int,
        metrics: AppMetrics,
    ) -> None:
        self.root = root
        self.compression = compression
        self.flush_rows = flush_rows
        self.flush_seconds = flush_seconds
        self.metrics = metrics
        self._buffers: dict[Path, list[dict[str, Any]]] = defaultdict(list)

    def _path_for(self, event: MarketEvent) -> Path:
        return (
            self.root
            / f"exchange={event.exchange_name.value}"
            / f"event_type={event.event_type}"
            / f"date={event.event_ts.strftime('%Y-%m-%d')}"
            / f"hour={event.event_ts.strftime('%H')}"
            / f"symbol={event.symbol}"
        )

    async def append(self, event: MarketEvent) -> None:
        path = self._path_for(event)
        self._buffers[path].append(event.model_dump(mode="json"))
        if len(self._buffers[path]) >= self.flush_rows:
            await self.flush()

    async def flush(self) -> None:
        if not self._buffers:
            return
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on local environment
            self.metrics.record_parquet_flush(success=False)
            raise RuntimeError("pyarrow dependency is required for parquet market archive writes") from exc

        pending = dict(self._buffers)
        self._buffers.clear()
        try:
            for path, rows in pending.items():
                path.mkdir(parents=True, exist_ok=True)
                normalized_rows = []
                for row in rows:
                    normalized_rows.append(
                        {
                            key: json.dumps(value, sort_keys=True) if isinstance(value, dict) else value
                            for key, value in row.items()
                        }
                    )
                table = pa.Table.from_pylist(normalized_rows)
                part_number = len(list(path.glob("*.parquet")))
                pq.write_table(
                    table,
                    path / f"part-{part_number:05d}.parquet",
                    compression=self.compression,
                )
        except Exception:
            for path, rows in pending.items():
                self._buffers[path][0:0] = rows
            self.metrics.record_parquet_flush(success=False)
            raise
        self.metrics.record_parquet_flush(success=True)
