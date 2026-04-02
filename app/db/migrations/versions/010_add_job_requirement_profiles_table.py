"""Add job_requirement_profiles table.

Revision ID: 010_add_job_requirement_profiles
Revises: 009_add_response_signals
Create Date: 2026-03-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "010_add_job_requirement_profiles"
down_revision: Union[str, None] = "009_add_response_signals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_requirement_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_requirements_id", sa.Integer(), nullable=False),
        sa.Column(
            "trait_expectations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "weights",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["job_requirements_id"], ["job_requirements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_job_requirement_profiles_id"),
        "job_requirement_profiles",
        ["id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_job_requirement_profiles_id"), table_name="job_requirement_profiles"
    )
    op.drop_table("job_requirement_profiles")
