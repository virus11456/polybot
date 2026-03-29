"""initial schema: markets, roan_signals, external_events, roan_performance

Revision ID: 0001_initial
Revises:
Create Date: 2026-03-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. markets
    op.create_table(
        "markets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("polymarket_id", sa.String(100), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("yes_price", sa.Numeric(5, 4), nullable=True),
        sa.Column("no_price", sa.Numeric(5, 4), nullable=True),
        sa.Column("liquidity", sa.Numeric(12, 2), nullable=True),
        sa.Column("end_timestamp", sa.String(50), nullable=True),
        sa.Column("rules", postgresql.JSONB(), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("NOW()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("polymarket_id"),
    )
    op.create_index("idx_markets_category", "markets", ["category"])
    op.create_index("idx_markets_updated_at", "markets", [sa.text("updated_at DESC")])

    # 2. roan_signals
    op.create_table(
        "roan_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
        sa.Column("signal_type", sa.String(20), nullable=True),
        sa.Column("profit_pct", sa.Numeric(6, 4), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("suggested_position", sa.Numeric(10, 2), nullable=True),
        sa.Column("telegram_msg_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("NOW()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_signals_status", "roan_signals", ["status"])
    op.create_index("idx_signals_created_at", "roan_signals", [sa.text("created_at DESC")])

    # 3. external_events
    op.create_table(
        "external_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category", sa.String(20), nullable=True),
        sa.Column("event_title", sa.Text(), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(), nullable=True),
        sa.Column("market_related_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("NOW()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_events_category", "external_events", ["category"])

    # 4. roan_performance
    op.create_table(
        "roan_performance",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("signals_sent", sa.Integer(), server_default="0", nullable=True),
        sa.Column("signals_profitable", sa.Integer(), server_default="0", nullable=True),
        sa.Column("total_profit_usd", sa.Numeric(10, 2), server_default="0", nullable=True),
        sa.Column("capital_used", sa.Numeric(10, 2), server_default="0", nullable=True),
        sa.PrimaryKeyConstraint("date"),
    )


def downgrade() -> None:
    op.drop_table("roan_performance")
    op.drop_index("idx_events_category", table_name="external_events")
    op.drop_table("external_events")
    op.drop_index("idx_signals_created_at", table_name="roan_signals")
    op.drop_index("idx_signals_status", table_name="roan_signals")
    op.drop_table("roan_signals")
    op.drop_index("idx_markets_updated_at", table_name="markets")
    op.drop_index("idx_markets_category", table_name="markets")
    op.drop_table("markets")
