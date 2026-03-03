from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class OperationalAlertSink(Protocol):
    async def broadcast(self, *, kind: str, severity: str, text: str) -> None: ...
