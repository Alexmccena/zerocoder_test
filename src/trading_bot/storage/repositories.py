from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_bot.domain.models import AccountState, FillState, Instrument, OrderState, PositionState
from trading_bot.storage.models import (
    AccountSnapshotRecord,
    ConfigSnapshotRecord,
    FillRecord,
    InstrumentRecord,
    OrderRecord,
    PositionRecord,
    RunSessionRecord,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class RunSessionRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def create(self, *, run_mode: str, environment: str, status: str) -> RunSessionRecord:
        record = RunSessionRecord(run_mode=run_mode, environment=environment, status=status)
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def mark_completed(self, run_session_id: str) -> None:
        async with self.session_factory() as session:
            record = await session.get(RunSessionRecord, run_session_id)
            if record is None:
                return
            record.status = "completed"
            record.ended_at = utc_now()
            await session.commit()

    async def mark_failed(self, run_session_id: str, *, reason: str | None = None) -> None:
        async with self.session_factory() as session:
            record = await session.get(RunSessionRecord, run_session_id)
            if record is None:
                return
            record.status = "failed" if reason is None else f"failed:{reason}"
            record.ended_at = utc_now()
            await session.commit()


@dataclass(slots=True)
class ConfigSnapshotRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def create(self, *, run_session_id: str | None, config_hash: str, config_json: dict) -> ConfigSnapshotRecord:
        record = ConfigSnapshotRecord(
            run_session_id=run_session_id,
            config_hash=config_hash,
            config_json=config_json,
        )
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record


@dataclass(slots=True)
class InstrumentRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def upsert_many(self, instruments: list[Instrument]) -> None:
        if not instruments:
            return
        payload: list[dict[str, Any]] = []
        for instrument in instruments:
            payload.append(
                {
                    "exchange_name": instrument.exchange_name.value,
                    "symbol": instrument.symbol,
                    "market_type": instrument.market_type.value,
                    "status": instrument.status,
                    "tick_size": instrument.tick_size,
                    "lot_size": instrument.lot_size,
                    "min_quantity": instrument.min_quantity,
                    "min_notional": instrument.min_notional,
                    "max_order_quantity": instrument.max_order_quantity,
                    "max_leverage": instrument.max_leverage,
                    "price_scale": instrument.price_scale,
                    "quote_asset": instrument.quote_asset,
                    "base_asset": instrument.base_asset,
                    "payload_json": instrument.raw_payload,
                    "updated_at": instrument.updated_at,
                }
            )
        statement = insert(InstrumentRecord).values(payload)
        statement = statement.on_conflict_do_update(
            constraint="uq_instruments_exchange_symbol",
            set_={
                "market_type": statement.excluded.market_type,
                "status": statement.excluded.status,
                "tick_size": statement.excluded.tick_size,
                "lot_size": statement.excluded.lot_size,
                "min_quantity": statement.excluded.min_quantity,
                "min_notional": statement.excluded.min_notional,
                "max_order_quantity": statement.excluded.max_order_quantity,
                "max_leverage": statement.excluded.max_leverage,
                "price_scale": statement.excluded.price_scale,
                "quote_asset": statement.excluded.quote_asset,
                "base_asset": statement.excluded.base_asset,
                "payload_json": statement.excluded.payload_json,
                "updated_at": statement.excluded.updated_at,
            },
        )
        async with self.session_factory() as session:
            await session.execute(statement)
            await session.commit()


@dataclass(slots=True)
class AccountSnapshotRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def create(self, *, run_session_id: str | None, account: AccountState) -> AccountSnapshotRecord:
        record = AccountSnapshotRecord(
            run_session_id=run_session_id,
            exchange_name=account.exchange_name.value,
            account_type=account.account_type,
            equity=account.equity,
            available_balance=account.available_balance,
            wallet_balance=account.wallet_balance,
            margin_balance=account.margin_balance,
            unrealized_pnl=account.unrealized_pnl,
            payload_json=account.raw_payload,
        )
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record


@dataclass(slots=True)
class OrderRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def upsert_from_exchange(self, *, run_session_id: str | None, order: OrderState) -> None:
        values = {
            "run_session_id": run_session_id,
            "exchange_name": order.exchange_name.value,
            "symbol": order.symbol,
            "side": order.side,
            "order_type": order.order_type,
            "status": order.status,
            "quantity": order.quantity,
            "price": order.average_price,
            "stop_price": None,
            "reduce_only": False,
            "exchange_order_id": order.exchange_order_id,
            "time_in_force": order.time_in_force,
            "payload_json": order.raw_payload,
            "created_at": order.created_at,
            "updated_at": order.updated_at,
        }
        async with self.session_factory() as session:
            if order.exchange_order_id:
                existing_id = await session.scalar(
                    select(OrderRecord.id).where(
                        OrderRecord.exchange_name == order.exchange_name.value,
                        OrderRecord.exchange_order_id == order.exchange_order_id,
                    )
                )
                if existing_id is not None:
                    record = await session.get(OrderRecord, existing_id)
                    if record is not None:
                        record.run_session_id = run_session_id
                        record.symbol = order.symbol
                        record.side = order.side
                        record.order_type = order.order_type
                        record.status = order.status
                        record.quantity = order.quantity
                        record.price = order.average_price
                        record.time_in_force = order.time_in_force
                        record.payload_json = order.raw_payload
                        record.updated_at = order.updated_at
                        await session.commit()
                        return
            record = OrderRecord(**values)
            session.add(record)
            await session.commit()


@dataclass(slots=True)
class FillRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def insert_if_new(self, fill: FillState) -> bool:
        async with self.session_factory() as session:
            if fill.exchange_fill_id:
                existing_id = await session.scalar(
                    select(FillRecord.id).where(
                        FillRecord.exchange_name == fill.exchange_name.value,
                        FillRecord.exchange_fill_id == fill.exchange_fill_id,
                    )
                )
                if existing_id is not None:
                    return False
            record = FillRecord(
                order_id=fill.order_id,
                exchange_name=fill.exchange_name.value,
                symbol=fill.symbol,
                side=fill.side,
                price=fill.price,
                quantity=fill.quantity,
                fee=fill.fee,
                liquidity_type=fill.liquidity_type,
                exchange_fill_id=fill.exchange_fill_id,
                payload_json=fill.raw_payload,
                filled_at=fill.filled_at,
            )
            session.add(record)
            await session.commit()
        return True


@dataclass(slots=True)
class PositionRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def upsert_snapshot(self, *, run_session_id: str | None, position: PositionState) -> None:
        async with self.session_factory() as session:
            existing_id = await session.scalar(
                select(PositionRecord.id).where(
                    PositionRecord.exchange_name == position.exchange_name.value,
                    PositionRecord.symbol == position.symbol,
                    PositionRecord.side == position.side,
                    PositionRecord.status == position.status,
                )
            )
            if existing_id is not None:
                record = await session.get(PositionRecord, existing_id)
                if record is not None:
                    record.run_session_id = run_session_id
                    record.quantity = position.quantity
                    record.entry_price = position.entry_price
                    record.mark_price = position.mark_price
                    record.leverage = position.leverage
                    record.realized_pnl = position.realized_pnl
                    record.unrealized_pnl = position.unrealized_pnl
                    record.payload_json = position.raw_payload
                    record.opened_at = position.opened_at
                    record.closed_at = position.closed_at
                    record.updated_at = position.updated_at
                    await session.commit()
                    return
            record = PositionRecord(
                run_session_id=run_session_id,
                exchange_name=position.exchange_name.value,
                symbol=position.symbol,
                side=position.side,
                quantity=position.quantity,
                entry_price=position.entry_price,
                mark_price=position.mark_price,
                status=position.status,
                leverage=position.leverage,
                realized_pnl=position.realized_pnl,
                unrealized_pnl=position.unrealized_pnl,
                payload_json=position.raw_payload,
                opened_at=position.opened_at,
                closed_at=position.closed_at,
                updated_at=position.updated_at,
            )
            session.add(record)
            await session.commit()
