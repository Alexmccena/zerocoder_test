from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from trading_bot.domain.models import (
    AccountState,
    FillState,
    Instrument,
    OrderState,
    PnlSnapshot,
    PositionState,
    RiskDecision,
)
from trading_bot.storage.models import (
    AccountSnapshotRecord,
    ConfigSnapshotRecord,
    FillRecord,
    InstrumentRecord,
    OrderRecord,
    PnlSnapshotRecord,
    PositionRecord,
    RiskDecisionRecord,
    SignalEventRecord,
    RunSessionRecord,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class RunSessionRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def create(
        self,
        *,
        run_mode: str,
        environment: str,
        status: str,
        execution_venue: str | None = None,
    ) -> RunSessionRecord:
        record = RunSessionRecord(
            run_mode=run_mode,
            environment=environment,
            execution_venue=execution_venue,
            status=status,
        )
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def mark_completed(self, run_session_id: str, *, summary_json: dict[str, Any] | None = None) -> None:
        async with self.session_factory() as session:
            record = await session.get(RunSessionRecord, run_session_id)
            if record is None:
                return
            record.status = "completed"
            record.summary_json = summary_json
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

    async def get_latest_live_session(self) -> RunSessionRecord | None:
        statement = (
            select(RunSessionRecord)
            .where(RunSessionRecord.run_mode == "live")
            .order_by(desc(RunSessionRecord.started_at))
            .limit(1)
        )
        async with self.session_factory() as session:
            return await session.scalar(statement)


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
            execution_venue=account.execution_venue.value,
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

    async def create_paper_snapshot(self, *, run_session_id: str, account: AccountState) -> AccountSnapshotRecord:
        return await self.create(run_session_id=run_session_id, account=account)


@dataclass(slots=True)
class OrderRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def upsert_from_exchange(self, *, run_session_id: str | None, order: OrderState) -> None:
        values = {
            "run_session_id": run_session_id,
            "exchange_name": order.exchange_name.value,
            "execution_venue": order.execution_venue.value,
            "symbol": order.symbol,
            "side": order.side,
            "order_type": order.order_type,
            "status": order.status,
            "quantity": order.quantity,
            "price": order.price,
            "stop_price": order.stop_price,
            "reduce_only": order.reduce_only,
            "exchange_order_id": order.exchange_order_id,
            "client_order_id": order.client_order_id,
            "intent_id": order.intent_id,
            "filled_quantity": order.filled_quantity,
            "average_price": order.average_price,
            "time_in_force": order.time_in_force,
            "payload_json": order.raw_payload,
            "submitted_at": order.submitted_at,
            "expires_at": order.expires_at,
            "cancel_reason": order.cancel_reason,
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
                        record.execution_venue = order.execution_venue.value
                        record.order_type = order.order_type
                        record.status = order.status
                        record.quantity = order.quantity
                        record.price = order.price
                        record.stop_price = order.stop_price
                        record.reduce_only = order.reduce_only
                        record.client_order_id = order.client_order_id
                        record.intent_id = order.intent_id
                        record.filled_quantity = order.filled_quantity
                        record.average_price = order.average_price
                        record.time_in_force = order.time_in_force
                        record.payload_json = order.raw_payload
                        record.submitted_at = order.submitted_at
                        record.expires_at = order.expires_at
                        record.cancel_reason = order.cancel_reason
                        record.updated_at = order.updated_at
                        await session.commit()
                        return
            record = OrderRecord(**values)
            session.add(record)
            await session.commit()

    async def create_paper_order(self, *, run_session_id: str, order: OrderState) -> OrderRecord:
        record = OrderRecord(
            id=order.order_id,
            run_session_id=run_session_id,
            exchange_name=order.exchange_name.value,
            execution_venue=order.execution_venue.value,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            status=order.status,
            quantity=order.quantity,
            price=order.price,
            stop_price=order.stop_price,
            reduce_only=order.reduce_only,
            exchange_order_id=order.exchange_order_id,
            client_order_id=order.client_order_id,
            intent_id=order.intent_id,
            filled_quantity=order.filled_quantity,
            average_price=order.average_price,
            time_in_force=order.time_in_force,
            payload_json=order.raw_payload,
            submitted_at=order.submitted_at,
            expires_at=order.expires_at,
            cancel_reason=order.cancel_reason,
            created_at=order.created_at,
            updated_at=order.updated_at,
        )
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record

    async def update_lifecycle(self, *, run_session_id: str, order: OrderState) -> None:
        async with self.session_factory() as session:
            record = await session.get(OrderRecord, order.order_id)
            if record is None:
                await self.create_paper_order(run_session_id=run_session_id, order=order)
                return
            record.run_session_id = run_session_id
            record.execution_venue = order.execution_venue.value
            record.status = order.status
            record.filled_quantity = order.filled_quantity
            record.average_price = order.average_price
            record.price = order.price
            record.stop_price = order.stop_price
            record.reduce_only = order.reduce_only
            record.expires_at = order.expires_at
            record.cancel_reason = order.cancel_reason
            record.payload_json = order.raw_payload
            record.updated_at = order.updated_at
            await session.commit()

    async def list_open_orders(
        self,
        *,
        run_session_id: str | None = None,
        symbol: str | None = None,
        execution_venue: str | None = None,
    ) -> list[OrderRecord]:
        statement = select(OrderRecord).where(
            OrderRecord.status.in_(("new", "working", "partially_filled")),
        )
        if run_session_id is not None:
            statement = statement.where(OrderRecord.run_session_id == run_session_id)
        if symbol is not None:
            statement = statement.where(OrderRecord.symbol == symbol)
        if execution_venue is not None:
            statement = statement.where(OrderRecord.execution_venue == execution_venue)
        async with self.session_factory() as session:
            result = await session.scalars(statement)
            return list(result)

    async def get_by_exchange_order_id(self, *, exchange_name: str, exchange_order_id: str) -> OrderRecord | None:
        statement = select(OrderRecord).where(
            OrderRecord.exchange_name == exchange_name,
            OrderRecord.exchange_order_id == exchange_order_id,
        )
        async with self.session_factory() as session:
            return await session.scalar(statement)

    async def get_by_client_order_id(self, *, exchange_name: str, client_order_id: str) -> OrderRecord | None:
        statement = select(OrderRecord).where(
            OrderRecord.exchange_name == exchange_name,
            OrderRecord.client_order_id == client_order_id,
        )
        async with self.session_factory() as session:
            return await session.scalar(statement)


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
                run_session_id=fill.run_session_id,
                order_id=fill.order_id,
                exchange_name=fill.exchange_name.value,
                execution_venue=fill.execution_venue.value,
                symbol=fill.symbol,
                side=fill.side,
                price=fill.price,
                quantity=fill.quantity,
                fee=fill.fee,
                fee_asset=fill.fee_asset,
                liquidity_type=fill.liquidity_type,
                is_maker=fill.is_maker,
                slippage_bps=fill.slippage_bps,
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
                    record.execution_venue = position.execution_venue.value
                    record.quantity = position.quantity
                    record.entry_price = position.entry_price
                    record.mark_price = position.mark_price
                    record.last_price = position.last_price
                    record.leverage = position.leverage
                    record.realized_pnl = position.realized_pnl
                    record.unrealized_pnl = position.unrealized_pnl
                    record.fees_paid = position.fees_paid
                    record.closed_reason = position.closed_reason
                    record.payload_json = position.raw_payload
                    record.opened_at = position.opened_at
                    record.closed_at = position.closed_at
                    record.updated_at = position.updated_at
                    await session.commit()
                    return
            record = PositionRecord(
                run_session_id=run_session_id,
                exchange_name=position.exchange_name.value,
                execution_venue=position.execution_venue.value,
                symbol=position.symbol,
                side=position.side,
                quantity=position.quantity,
                entry_price=position.entry_price,
                mark_price=position.mark_price,
                last_price=position.last_price,
                status=position.status,
                leverage=position.leverage,
                realized_pnl=position.realized_pnl,
                unrealized_pnl=position.unrealized_pnl,
                fees_paid=position.fees_paid,
                closed_reason=position.closed_reason,
                payload_json=position.raw_payload,
                opened_at=position.opened_at,
                closed_at=position.closed_at,
                updated_at=position.updated_at,
            )
            session.add(record)
            await session.commit()

    async def get_open_by_symbol(self, *, run_session_id: str, symbol: str) -> PositionRecord | None:
        statement = select(PositionRecord).where(
            PositionRecord.run_session_id == run_session_id,
            PositionRecord.symbol == symbol,
            PositionRecord.status == "open",
        )
        async with self.session_factory() as session:
            return await session.scalar(statement)

    async def list_open_positions(
        self,
        *,
        run_session_id: str | None = None,
        symbol: str | None = None,
        execution_venue: str | None = None,
    ) -> list[PositionRecord]:
        statement = select(PositionRecord).where(PositionRecord.status == "open")
        if run_session_id is not None:
            statement = statement.where(PositionRecord.run_session_id == run_session_id)
        if symbol is not None:
            statement = statement.where(PositionRecord.symbol == symbol)
        if execution_venue is not None:
            statement = statement.where(PositionRecord.execution_venue == execution_venue)
        async with self.session_factory() as session:
            result = await session.scalars(statement)
            return list(result)

    async def close_position(self, *, run_session_id: str, position: PositionState) -> None:
        async with self.session_factory() as session:
            statement = select(PositionRecord).where(
                PositionRecord.run_session_id == run_session_id,
                PositionRecord.symbol == position.symbol,
                PositionRecord.status == "open",
            )
            record = await session.scalar(statement)
            if record is None:
                await self.upsert_snapshot(run_session_id=run_session_id, position=position)
                return
            record.execution_venue = position.execution_venue.value
            record.quantity = position.quantity
            record.entry_price = position.entry_price
            record.mark_price = position.mark_price
            record.last_price = position.last_price
            record.status = position.status
            record.realized_pnl = position.realized_pnl
            record.unrealized_pnl = position.unrealized_pnl
            record.fees_paid = position.fees_paid
            record.closed_reason = position.closed_reason
            record.payload_json = position.raw_payload
            record.closed_at = position.closed_at
            record.updated_at = position.updated_at
            await session.commit()


@dataclass(slots=True)
class SignalEventRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def create(
        self,
        *,
        run_session_id: str,
        symbol: str,
        strategy_name: str,
        signal_type: str,
        payload_json: dict[str, Any],
    ) -> SignalEventRecord:
        record = SignalEventRecord(
            run_session_id=run_session_id,
            symbol=symbol,
            strategy_name=strategy_name,
            signal_type=signal_type,
            payload_json=payload_json,
        )
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record


@dataclass(slots=True)
class RiskDecisionRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def create(
        self,
        *,
        run_session_id: str,
        symbol: str,
        intent_id: str,
        signal_event_id: str | None,
        decision: RiskDecision,
    ) -> RiskDecisionRecord:
        record = RiskDecisionRecord(
            signal_event_id=signal_event_id,
            run_session_id=run_session_id,
            symbol=symbol,
            intent_id=intent_id,
            decision=decision.decision.value,
            reasons_json=decision.reasons,
            payload_json=decision.payload,
            created_at=decision.created_at,
        )
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record


@dataclass(slots=True)
class PnlSnapshotRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def append(self, snapshot: PnlSnapshot) -> PnlSnapshotRecord:
        record = PnlSnapshotRecord(
            run_session_id=snapshot.run_session_id or "",
            execution_venue=snapshot.execution_venue.value,
            event_ts=snapshot.event_ts,
            equity=snapshot.equity,
            balance=snapshot.balance,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
            drawdown=snapshot.drawdown,
            payload_json=snapshot.payload,
        )
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record
