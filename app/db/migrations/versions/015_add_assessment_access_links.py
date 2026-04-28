"""Add secure one-time interview access links.

Revision ID: 015_add_assessment_access_links
Revises: 014_add_created_at_to_models
Create Date: 2026-04-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015_add_assessment_access_links"
down_revision: Union[str, None] = "014_add_created_at_to_models"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assessment_access_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("token_salt", sa.String(length=64), nullable=False),
        sa.Column("candidate_email", sa.String(length=255), nullable=True),
        sa.Column("issued_reason", sa.Text(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_assessment_access_links_token_hash"),
    )
    op.create_index(op.f("ix_assessment_access_links_id"), "assessment_access_links", ["id"], unique=False)
    op.create_index(
        op.f("ix_assessment_access_links_assessment_id"),
        "assessment_access_links",
        ["assessment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assessment_access_links_created_by_user_id"),
        "assessment_access_links",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assessment_access_links_token_hash"),
        "assessment_access_links",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_assessment_access_links_expires_at"),
        "assessment_access_links",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_assessment_access_links_expires_at"), table_name="assessment_access_links")
    op.drop_index(op.f("ix_assessment_access_links_token_hash"), table_name="assessment_access_links")
    op.drop_index(op.f("ix_assessment_access_links_created_by_user_id"), table_name="assessment_access_links")
    op.drop_index(op.f("ix_assessment_access_links_assessment_id"), table_name="assessment_access_links")
    op.drop_index(op.f("ix_assessment_access_links_id"), table_name="assessment_access_links")
    op.drop_table("assessment_access_links")
