"""Add assessment_context_embeddings table for RAG (requirements + questions as vectors).

Revision ID: 005_embeddings
Revises: 004_drop_category_is_required
Create Date: 2025-03-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "005_embeddings"
down_revision: Union[str, None] = "004_drop_category_is_required"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "assessment_context_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("job_requirements_id", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_requirements_id"], ["job_requirements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_assessment_context_embeddings_id"),
        "assessment_context_embeddings",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assessment_context_embeddings_assessment_id"),
        "assessment_context_embeddings",
        ["assessment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assessment_context_embeddings_job_requirements_id"),
        "assessment_context_embeddings",
        ["job_requirements_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_assessment_context_embeddings_content_type"),
        "assessment_context_embeddings",
        ["content_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_assessment_context_embeddings_content_type"),
        table_name="assessment_context_embeddings",
    )
    op.drop_index(
        op.f("ix_assessment_context_embeddings_job_requirements_id"),
        table_name="assessment_context_embeddings",
    )
    op.drop_index(
        op.f("ix_assessment_context_embeddings_assessment_id"),
        table_name="assessment_context_embeddings",
    )
    op.drop_index(
        op.f("ix_assessment_context_embeddings_id"),
        table_name="assessment_context_embeddings",
    )
    op.drop_table("assessment_context_embeddings")
    # Optionally: op.execute("DROP EXTENSION IF EXISTS vector")
    # Skipping DROP EXTENSION so other DB objects don't depend on it
