"""Add grid strategy state tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_grid_strategy_state"
down_revision = "0003_phase3_paper_replay_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grid_pair_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("leverage", sa.Numeric(10, 4), nullable=False, server_default="1"),
        sa.Column("budget_quote", sa.Numeric(20, 8), nullable=False),
        sa.Column("stack_size_quote", sa.Numeric(20, 8), nullable=False),
        sa.Column("corridor_pct", sa.Numeric(10, 4), nullable=False),
        sa.Column("take_profit_pct", sa.Numeric(10, 4), nullable=False),
        sa.Column("orders_per_stack", sa.Integer(), nullable=False),
        sa.Column("lower_threshold_pct", sa.Numeric(10, 4), nullable=False),
        sa.Column("upper_threshold_pct", sa.Numeric(10, 4), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_session_id", "symbol", name="uq_grid_pair_profiles_run_symbol"),
    )
    op.create_table(
        "grid_pair_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_stacks", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active_stacks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_stack_anchor", sa.Numeric(20, 8), nullable=True),
        sa.Column("last_realized_sell_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("last_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("state_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_session_id", "symbol", name="uq_grid_pair_snapshots_run_symbol"),
    )
    op.create_table(
        "grid_order_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("order_id", sa.String(length=128), nullable=False),
        sa.Column("exchange_order_id", sa.String(length=128), nullable=True),
        sa.Column("client_order_id", sa.String(length=128), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("stack_index", sa.Integer(), nullable=True),
        sa.Column("level_index", sa.Integer(), nullable=True),
        sa.Column("parent_order_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_session_id", "order_id", name="uq_grid_order_links_run_order"),
    )
    op.create_index("ix_grid_order_links_exchange_order_id", "grid_order_links", ["exchange_order_id"])
    op.create_index("ix_grid_order_links_client_order_id", "grid_order_links", ["client_order_id"])
    op.create_table(
        "grid_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_session_id", sa.String(length=36), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_grid_events_run_symbol_created_at",
        "grid_events",
        ["run_session_id", "symbol", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_grid_events_run_symbol_created_at", table_name="grid_events")
    op.drop_table("grid_events")
    op.drop_index("ix_grid_order_links_client_order_id", table_name="grid_order_links")
    op.drop_index("ix_grid_order_links_exchange_order_id", table_name="grid_order_links")
    op.drop_table("grid_order_links")
    op.drop_table("grid_pair_snapshots")
    op.drop_table("grid_pair_profiles")
