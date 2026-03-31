"""add entry_price, target_price, stop_loss, direction to roan_signals

Revision ID: 0002_signal_prices
Revises: 0001_initial
Create Date: 2026-03-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_signal_prices"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("roan_signals", sa.Column("entry_price", sa.Numeric(6, 4), nullable=True))
    op.add_column("roan_signals", sa.Column("target_price", sa.Numeric(6, 4), nullable=True))
    op.add_column("roan_signals", sa.Column("stop_loss", sa.Numeric(6, 4), nullable=True))
    op.add_column("roan_signals", sa.Column("direction", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("roan_signals", "direction")
    op.drop_column("roan_signals", "stop_loss")
    op.drop_column("roan_signals", "target_price")
    op.drop_column("roan_signals", "entry_price")
