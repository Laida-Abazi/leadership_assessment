"""Add JSONB intelligence columns to analysis table.

Revision ID: 011_alter_analysis_add_jsonb_columns
Revises: 010_add_job_requirement_profiles
Create Date: 2026-03-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011_add_analysis_jsonb"
down_revision: Union[str, None] = "010_add_job_requirement_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis",
        sa.Column(
            "aggregated_traits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "analysis",
        sa.Column(
            "consistency_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "analysis",
        sa.Column(
            "trait_gaps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "analysis",
        sa.Column(
            "contradictions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "analysis",
        sa.Column(
            "behavioral_patterns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("analysis", "behavioral_patterns")
    op.drop_column("analysis", "contradictions")
    op.drop_column("analysis", "trait_gaps")
    op.drop_column("analysis", "consistency_scores")
    op.drop_column("analysis", "aggregated_traits")
