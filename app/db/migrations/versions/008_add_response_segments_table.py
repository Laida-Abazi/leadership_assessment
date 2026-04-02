"""Add response_segments table.

Revision ID: 008_add_response_segments
Revises: 007_reset_password_token
Create Date: 2026-03-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008_add_response_segments"
down_revision: Union[str, None] = "007_reset_password_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "response_segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("response_type", sa.String(64), nullable=False),
        sa.Column("question_id", sa.String(128), nullable=True),
        sa.Column("segment_text", sa.Text(), nullable=False),
        sa.Column("sequence_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["assessments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_response_segments_id"), "response_segments", ["id"], unique=False
    )
    op.create_index(
        "idx_response_segments_assessment",
        "response_segments",
        ["assessment_id"],
        unique=False,
    )
    op.create_index(
        "idx_response_segments_type",
        "response_segments",
        ["assessment_id", "response_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_response_segments_type", table_name="response_segments")
    op.drop_index("idx_response_segments_assessment", table_name="response_segments")
    op.drop_index(op.f("ix_response_segments_id"), table_name="response_segments")
    op.drop_table("response_segments")
