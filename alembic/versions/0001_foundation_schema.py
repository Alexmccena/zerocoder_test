"""Create foundation schema."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_mode", sa.String(length=32), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "config_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=True),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "signal_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("strategy_name", sa.String(length=128), nullable=False),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "risk_decisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("signal_event_id", sa.String(length=36), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=True),
        sa.Column("exchange_name", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("order_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("price", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exchange_order_id", sa.String(length=128), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "fills",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("price", sa.Numeric(20, 8), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("fee", sa.Numeric(20, 8), nullable=False),
        sa.Column("liquidity_type", sa.String(length=32), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "positions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=True),
        sa.Column("exchange_name", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("leverage", sa.Numeric(10, 4), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(20, 8), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "llm_advice",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("advice_type", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("llm_advice")
    op.drop_table("positions")
    op.drop_table("fills")
    op.drop_table("orders")
    op.drop_table("risk_decisions")
    op.drop_table("signal_events")
    op.drop_table("config_snapshots")
    op.drop_table("run_sessions")
