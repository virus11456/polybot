"""add slug column to markets table

Revision ID: 0003_markets_slug
Revises: 0002_signal_prices
Create Date: 2026-03-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_markets_slug"
down_revision: Union[str, None] = "0002_signal_prices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("markets", sa.Column("slug", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("markets", "slug")
