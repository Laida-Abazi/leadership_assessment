"""Drop category and is_required from job_requirements.

Revision ID: 004_drop_category_is_required
Revises: 003_category_null
Create Date: 2025-03-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004_drop_category_is_required"
down_revision: Union[str, None] = "003_category_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("job_requirements", "category")
    op.drop_column("job_requirements", "is_required")


def downgrade() -> None:
    op.add_column("job_requirements", sa.Column("category", sa.String(), nullable=True))
    op.add_column("job_requirements", sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")))
