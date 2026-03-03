"""Add paper runtime and replay/backtest persistence fields."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_phase3_paper_replay_runtime"
down_revision = "0002_phase2_bybit_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run_sessions", sa.Column("execution_venue", sa.String(length=16), nullable=True))
    op.add_column("run_sessions", sa.Column("summary_json", sa.JSON(), nullable=True))

    op.add_column(
        "account_snapshots",
        sa.Column("execution_venue", sa.String(length=16), nullable=False, server_default="live"),
    )

    op.add_column("risk_decisions", sa.Column("run_session_id", sa.String(length=36), nullable=True))
    op.add_column("risk_decisions", sa.Column("symbol", sa.String(length=32), nullable=True))
    op.add_column("risk_decisions", sa.Column("intent_id", sa.String(length=36), nullable=True))

    op.add_column("orders", sa.Column("execution_venue", sa.String(length=16), nullable=False, server_default="live"))
    op.add_column("orders", sa.Column("client_order_id", sa.String(length=128), nullable=True))
    op.add_column("orders", sa.Column("intent_id", sa.String(length=36), nullable=True))
    op.add_column("orders", sa.Column("filled_quantity", sa.Numeric(20, 8), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("average_price", sa.Numeric(20, 8), nullable=True))
    op.add_column(
        "orders",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("orders", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("cancel_reason", sa.String(length=128), nullable=True))

    op.add_column("fills", sa.Column("run_session_id", sa.String(length=36), nullable=True))
    op.add_column("fills", sa.Column("execution_venue", sa.String(length=16), nullable=False, server_default="live"))
    op.add_column("fills", sa.Column("fee_asset", sa.String(length=16), nullable=False, server_default="USDT"))
    op.add_column("fills", sa.Column("is_maker", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("fills", sa.Column("slippage_bps", sa.Numeric(20, 8), nullable=False, server_default="0"))

    op.add_column("positions", sa.Column("execution_venue", sa.String(length=16), nullable=False, server_default="live"))
    op.add_column("positions", sa.Column("last_price", sa.Numeric(20, 8), nullable=True))
    op.add_column("positions", sa.Column("fees_paid", sa.Numeric(20, 8), nullable=False, server_default="0"))
    op.add_column("positions", sa.Column("closed_reason", sa.String(length=128), nullable=True))

    op.create_table(
        "pnl_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=False),
        sa.Column("execution_venue", sa.String(length=16), nullable=False),
        sa.Column("event_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity", sa.Numeric(20, 8), nullable=False),
        sa.Column("balance", sa.Numeric(20, 8), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(20, 8), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(20, 8), nullable=False),
        sa.Column("drawdown", sa.Numeric(20, 8), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("pnl_snapshots")

    op.drop_column("positions", "closed_reason")
    op.drop_column("positions", "fees_paid")
    op.drop_column("positions", "last_price")
    op.drop_column("positions", "execution_venue")

    op.drop_column("fills", "slippage_bps")
    op.drop_column("fills", "is_maker")
    op.drop_column("fills", "fee_asset")
    op.drop_column("fills", "execution_venue")
    op.drop_column("fills", "run_session_id")

    op.drop_column("orders", "cancel_reason")
    op.drop_column("orders", "expires_at")
    op.drop_column("orders", "submitted_at")
    op.drop_column("orders", "average_price")
    op.drop_column("orders", "filled_quantity")
    op.drop_column("orders", "intent_id")
    op.drop_column("orders", "client_order_id")
    op.drop_column("orders", "execution_venue")

    op.drop_column("risk_decisions", "intent_id")
    op.drop_column("risk_decisions", "symbol")
    op.drop_column("risk_decisions", "run_session_id")

    op.drop_column("account_snapshots", "execution_venue")

    op.drop_column("run_sessions", "summary_json")
    op.drop_column("run_sessions", "execution_venue")
