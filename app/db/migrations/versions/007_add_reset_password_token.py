"""Add reset_password_token column to users table.

Revision ID: 007_reset_password_token
Revises: 006_email_verification
Create Date: 2026-03-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "007_reset_password_token"
down_revision: Union[str, None] = "006_email_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("reset_password_token", sa.String(), nullable=True),
    )
    op.create_index(
        op.f("ix_users_reset_password_token"),
        "users",
        ["reset_password_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_reset_password_token"), table_name="users")
    op.drop_column("users", "reset_password_token")
