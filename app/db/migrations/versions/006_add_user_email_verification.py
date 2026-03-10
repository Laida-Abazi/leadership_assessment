"""Add is_verified and verification_token columns to users table.

Revision ID: 006_email_verification
Revises: 005_embeddings
Create Date: 2026-03-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006_email_verification"
down_revision: Union[str, None] = "005_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "users",
        sa.Column("verification_token", sa.String(), nullable=True),
    )
    op.create_index(
        op.f("ix_users_verification_token"),
        "users",
        ["verification_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_verification_token"), table_name="users")
    op.drop_column("users", "verification_token")
    op.drop_column("users", "is_verified")
