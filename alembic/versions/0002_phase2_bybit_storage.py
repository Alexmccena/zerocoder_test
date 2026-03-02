"""Extend storage for Bybit market data capture."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_phase2_bybit_storage"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("exchange_name", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("market_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tick_size", sa.Numeric(20, 8), nullable=False),
        sa.Column("lot_size", sa.Numeric(20, 8), nullable=False),
        sa.Column("min_quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("min_notional", sa.Numeric(20, 8), nullable=True),
        sa.Column("max_order_quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("max_leverage", sa.Numeric(20, 8), nullable=True),
        sa.Column("price_scale", sa.Integer(), nullable=True),
        sa.Column("quote_asset", sa.String(length=16), nullable=False),
        sa.Column("base_asset", sa.String(length=16), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exchange_name", "symbol", name="uq_instruments_exchange_symbol"),
    )
    op.create_table(
        "account_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=True),
        sa.Column("exchange_name", sa.String(length=32), nullable=False),
        sa.Column("account_type", sa.String(length=32), nullable=False),
        sa.Column("equity", sa.Numeric(20, 8), nullable=False),
        sa.Column("available_balance", sa.Numeric(20, 8), nullable=False),
        sa.Column("wallet_balance", sa.Numeric(20, 8), nullable=False),
        sa.Column("margin_balance", sa.Numeric(20, 8), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(20, 8), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("orders", sa.Column("time_in_force", sa.String(length=32), nullable=True))
    op.create_index("ix_orders_exchange_order_id", "orders", ["exchange_name", "exchange_order_id"])

    op.add_column("fills", sa.Column("exchange_name", sa.String(length=32), nullable=False, server_default="bybit"))
    op.add_column("fills", sa.Column("symbol", sa.String(length=32), nullable=False, server_default=""))
    op.add_column("fills", sa.Column("side", sa.String(length=16), nullable=False, server_default=""))
    op.add_column("fills", sa.Column("exchange_fill_id", sa.String(length=128), nullable=True))
    op.add_column("fills", sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.create_index("ix_fills_exchange_fill_id", "fills", ["exchange_name", "exchange_fill_id"])

    op.add_column("positions", sa.Column("mark_price", sa.Numeric(20, 8), nullable=True))
    op.add_column(
        "positions",
        sa.Column("unrealized_pnl", sa.Numeric(20, 8), nullable=False, server_default="0"),
    )
    op.add_column("positions", sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column(
        "positions",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_positions_exchange_symbol_status", "positions", ["exchange_name", "symbol", "status"])


def downgrade() -> None:
    op.drop_index("ix_positions_exchange_symbol_status", table_name="positions")
    op.drop_column("positions", "updated_at")
    op.drop_column("positions", "payload_json")
    op.drop_column("positions", "unrealized_pnl")
    op.drop_column("positions", "mark_price")

    op.drop_index("ix_fills_exchange_fill_id", table_name="fills")
    op.drop_column("fills", "payload_json")
    op.drop_column("fills", "exchange_fill_id")
    op.drop_column("fills", "side")
    op.drop_column("fills", "symbol")
    op.drop_column("fills", "exchange_name")

    op.drop_index("ix_orders_exchange_order_id", table_name="orders")
    op.drop_column("orders", "time_in_force")

    op.drop_table("account_snapshots")
    op.drop_table("instruments")
