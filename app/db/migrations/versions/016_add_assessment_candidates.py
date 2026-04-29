"""Add candidate tracking for interview links.

Revision ID: 016_add_assessment_candidates
Revises: 015_add_assessment_access_links
Create Date: 2026-04-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "016_add_assessment_candidates"
down_revision: Union[str, None] = "015_add_assessment_access_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assessment_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("access_link_id", sa.Integer(), nullable=True),
        sa.Column("first_name", sa.String(length=120), nullable=False),
        sa.Column("last_name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("assessment_type_code", sa.String(length=64), nullable=False),
        sa.Column("analysis_snapshot", sa.Text(), nullable=True),
        sa.Column("prediction_snapshot", sa.Text(), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("risk_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("link_token", sa.String(length=255), nullable=False),
        sa.Column("link_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("link_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["access_link_id"], ["assessment_access_links.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("access_link_id", name="uq_assessment_candidates_access_link_id"),
    )
    op.create_index(op.f("ix_assessment_candidates_id"), "assessment_candidates", ["id"], unique=False)
    op.create_index(
        op.f("ix_assessment_candidates_assessment_id"),
        "assessment_candidates",
        ["assessment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assessment_candidates_access_link_id"),
        "assessment_candidates",
        ["access_link_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_assessment_candidates_email"),
        "assessment_candidates",
        ["email"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_assessment_candidates_email"), table_name="assessment_candidates")
    op.drop_index(op.f("ix_assessment_candidates_access_link_id"), table_name="assessment_candidates")
    op.drop_index(op.f("ix_assessment_candidates_assessment_id"), table_name="assessment_candidates")
    op.drop_index(op.f("ix_assessment_candidates_id"), table_name="assessment_candidates")
    op.drop_table("assessment_candidates")
