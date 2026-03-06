"""Make job_requirements.category nullable.

Revision ID: 003_category_null
Revises: 002_job_req
Create Date: 2025-03-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "003_category_null"
down_revision: Union[str, None] = "002_job_req"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "job_requirements",
        "category",
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "job_requirements",
        "category",
        existing_type=sa.String(),
        nullable=False,
    )
