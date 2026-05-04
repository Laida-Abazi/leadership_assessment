"""Add candidate-scoped analysis pipeline columns.

Revision ID: 017_candidate_scoped_analysis
Revises: 016_add_assessment_candidates
Create Date: 2026-05-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017_candidate_scoped_analysis"
down_revision: Union[str, None] = "016_add_assessment_candidates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES_WITH_CANDIDATE_SCOPE = (
    "assessment_answers",
    "responses",
    "response_segments",
    "response_signals",
    "analysis",
    "assessment_results",
)


def upgrade() -> None:
    op.add_column("assessment_answers", sa.Column("candidate_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_assessment_answers_candidate_id"), "assessment_answers", ["candidate_id"], unique=False)
    op.create_foreign_key(
        "fk_assessment_answers_candidate_id_assessment_candidates",
        "assessment_answers",
        "assessment_candidates",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column("responses", sa.Column("candidate_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_responses_candidate_id"), "responses", ["candidate_id"], unique=False)
    op.create_foreign_key(
        "fk_responses_candidate_id_assessment_candidates",
        "responses",
        "assessment_candidates",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column("response_segments", sa.Column("candidate_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_response_segments_candidate_id"), "response_segments", ["candidate_id"], unique=False)
    op.create_foreign_key(
        "fk_response_segments_candidate_id_assessment_candidates",
        "response_segments",
        "assessment_candidates",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column("response_signals", sa.Column("candidate_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_response_signals_candidate_id"), "response_signals", ["candidate_id"], unique=False)
    op.create_foreign_key(
        "fk_response_signals_candidate_id_assessment_candidates",
        "response_signals",
        "assessment_candidates",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column("analysis", sa.Column("candidate_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_analysis_candidate_id"), "analysis", ["candidate_id"], unique=False)
    op.create_foreign_key(
        "fk_analysis_candidate_id_assessment_candidates",
        "analysis",
        "assessment_candidates",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column("assessment_results", sa.Column("candidate_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_assessment_results_candidate_id"), "assessment_results", ["candidate_id"], unique=False)
    op.create_foreign_key(
        "fk_assessment_results_candidate_id_assessment_candidates",
        "assessment_results",
        "assessment_candidates",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )

    for table_name in TABLES_WITH_CANDIDATE_SCOPE:
        op.execute(
            sa.text(
                f"""
                UPDATE {table_name} AS target
                SET candidate_id = candidate.id
                FROM assessment_candidates AS candidate
                WHERE target.assessment_id = candidate.assessment_id
                  AND (
                    SELECT COUNT(*)
                    FROM assessment_candidates AS scoped
                    WHERE scoped.assessment_id = target.assessment_id
                  ) = 1
                """
            )
        )

    op.drop_constraint("uq_assessment_answers_assessment_key", "assessment_answers", type_="unique")
    op.create_unique_constraint(
        "uq_assessment_answers_scope_key",
        "assessment_answers",
        ["assessment_id", "candidate_id", "item_key"],
    )

    op.drop_index(op.f("ix_assessment_results_assessment_id"), table_name="assessment_results")
    op.create_index(
        op.f("ix_assessment_results_assessment_id"),
        "assessment_results",
        ["assessment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_assessment_results_assessment_id"), table_name="assessment_results")
    op.create_index(
        op.f("ix_assessment_results_assessment_id"),
        "assessment_results",
        ["assessment_id"],
        unique=True,
    )

    op.drop_constraint("uq_assessment_answers_scope_key", "assessment_answers", type_="unique")
    op.create_unique_constraint(
        "uq_assessment_answers_assessment_key",
        "assessment_answers",
        ["assessment_id", "item_key"],
    )

    op.drop_constraint("fk_assessment_results_candidate_id_assessment_candidates", "assessment_results", type_="foreignkey")
    op.drop_index(op.f("ix_assessment_results_candidate_id"), table_name="assessment_results")
    op.drop_column("assessment_results", "candidate_id")

    op.drop_constraint("fk_analysis_candidate_id_assessment_candidates", "analysis", type_="foreignkey")
    op.drop_index(op.f("ix_analysis_candidate_id"), table_name="analysis")
    op.drop_column("analysis", "candidate_id")

    op.drop_constraint("fk_response_signals_candidate_id_assessment_candidates", "response_signals", type_="foreignkey")
    op.drop_index(op.f("ix_response_signals_candidate_id"), table_name="response_signals")
    op.drop_column("response_signals", "candidate_id")

    op.drop_constraint("fk_response_segments_candidate_id_assessment_candidates", "response_segments", type_="foreignkey")
    op.drop_index(op.f("ix_response_segments_candidate_id"), table_name="response_segments")
    op.drop_column("response_segments", "candidate_id")

    op.drop_constraint("fk_responses_candidate_id_assessment_candidates", "responses", type_="foreignkey")
    op.drop_index(op.f("ix_responses_candidate_id"), table_name="responses")
    op.drop_column("responses", "candidate_id")

    op.drop_constraint("fk_assessment_answers_candidate_id_assessment_candidates", "assessment_answers", type_="foreignkey")
    op.drop_index(op.f("ix_assessment_answers_candidate_id"), table_name="assessment_answers")
    op.drop_column("assessment_answers", "candidate_id")
