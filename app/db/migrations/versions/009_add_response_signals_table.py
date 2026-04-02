"""Add response_signals table.

Revision ID: 009_add_response_signals
Revises: 008_add_response_segments
Create Date: 2026-03-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009_add_response_signals"
down_revision: Union[str, None] = "008_add_response_segments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "response_signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("response_segment_id", sa.Integer(), nullable=True),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("response_type", sa.String(64), nullable=False),
        sa.Column("traits", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("strengths", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("risks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["assessments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["response_segment_id"], ["response_segments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_response_signals_id"), "response_signals", ["id"], unique=False
    )
    op.create_index(
        "idx_response_signals_assessment",
        "response_signals",
        ["assessment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_response_signals_assessment", table_name="response_signals")
    op.drop_index(op.f("ix_response_signals_id"), table_name="response_signals")
    op.drop_table("response_signals")
