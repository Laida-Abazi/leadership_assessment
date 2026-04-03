"""Add assessment type registry and canonical item/answer/result tables.

Revision ID: 013_add_multi_assessment_tables
Revises: 012_add_pred_fit
Create Date: 2026-04-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "013_add_multi_assessment_tables"
down_revision: Union[str, None] = "012_add_pred_fit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assessments",
        sa.Column("assessment_type_code", sa.String(length=64), server_default="leadership_core", nullable=False),
    )
    op.add_column(
        "assessments",
        sa.Column("assessment_version", sa.String(length=32), server_default="v1", nullable=False),
    )
    op.add_column(
        "assessments",
        sa.Column("assessment_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_table(
        "assessment_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assessment_types_id"), "assessment_types", ["id"], unique=False)
    op.create_index(op.f("ix_assessment_types_code"), "assessment_types", ["code"], unique=True)

    op.create_table(
        "assessment_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(length=128), nullable=False),
        sa.Column("display_label", sa.String(length=128), nullable=False),
        sa.Column("item_order", sa.Integer(), nullable=False),
        sa.Column("item_kind", sa.String(length=64), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("item_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assessment_id", "item_key", name="uq_assessment_items_assessment_key"),
    )
    op.create_index(op.f("ix_assessment_items_id"), "assessment_items", ["id"], unique=False)
    op.create_index(op.f("ix_assessment_items_assessment_id"), "assessment_items", ["assessment_id"], unique=False)
    op.create_index(op.f("ix_assessment_items_item_key"), "assessment_items", ["item_key"], unique=False)

    op.create_table(
        "assessment_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("assessment_item_id", sa.Integer(), nullable=True),
        sa.Column("item_key", sa.String(length=128), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("answer_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assessment_item_id"], ["assessment_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assessment_id", "item_key", name="uq_assessment_answers_assessment_key"),
    )
    op.create_index(op.f("ix_assessment_answers_id"), "assessment_answers", ["id"], unique=False)
    op.create_index(op.f("ix_assessment_answers_assessment_id"), "assessment_answers", ["assessment_id"], unique=False)
    op.create_index(op.f("ix_assessment_answers_assessment_item_id"), "assessment_answers", ["assessment_item_id"], unique=False)
    op.create_index(op.f("ix_assessment_answers_item_key"), "assessment_answers", ["item_key"], unique=False)

    op.create_table(
        "assessment_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("shared_result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("type_result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=False, server_default=""),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("risk_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assessment_id"),
    )
    op.create_index(op.f("ix_assessment_results_id"), "assessment_results", ["id"], unique=False)
    op.create_index(op.f("ix_assessment_results_assessment_id"), "assessment_results", ["assessment_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_assessment_results_assessment_id"), table_name="assessment_results")
    op.drop_index(op.f("ix_assessment_results_id"), table_name="assessment_results")
    op.drop_table("assessment_results")

    op.drop_index(op.f("ix_assessment_answers_item_key"), table_name="assessment_answers")
    op.drop_index(op.f("ix_assessment_answers_assessment_item_id"), table_name="assessment_answers")
    op.drop_index(op.f("ix_assessment_answers_assessment_id"), table_name="assessment_answers")
    op.drop_index(op.f("ix_assessment_answers_id"), table_name="assessment_answers")
    op.drop_table("assessment_answers")

    op.drop_index(op.f("ix_assessment_items_item_key"), table_name="assessment_items")
    op.drop_index(op.f("ix_assessment_items_assessment_id"), table_name="assessment_items")
    op.drop_index(op.f("ix_assessment_items_id"), table_name="assessment_items")
    op.drop_table("assessment_items")

    op.drop_index(op.f("ix_assessment_types_code"), table_name="assessment_types")
    op.drop_index(op.f("ix_assessment_types_id"), table_name="assessment_types")
    op.drop_table("assessment_types")

    op.drop_column("assessments", "assessment_metadata")
    op.drop_column("assessments", "assessment_version")
    op.drop_column("assessments", "assessment_type_code")
