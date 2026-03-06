"""Initial schema: users, job_requirements, assessments, responses, analysis, predictions.

Revision ID: 001_initial
Revises:
Create Date: 2025-03-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("surname", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "job_requirements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_requirements_id"), "job_requirements", ["id"], unique=False)
    op.create_index(op.f("ix_job_requirements_job_id"), "job_requirements", ["job_id"], unique=False)

    op.create_table(
        "assessments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_requirements_id", sa.Integer(), nullable=False),
        sa.Column("behavioral_question", sa.Text(), nullable=True),
        sa.Column("competency_based_question", sa.Text(), nullable=True),
        sa.Column("situational_question", sa.Text(), nullable=True),
        sa.Column("panel_question", sa.Text(), nullable=True),
        sa.Column("business_case_question", sa.Text(), nullable=True),
        sa.Column("live_simulation_question", sa.Text(), nullable=True),
        sa.Column("psychometric_question", sa.Text(), nullable=True),
        sa.Column("structured_reference_question", sa.Text(), nullable=True),
        sa.Column("culture_alignment_question", sa.Text(), nullable=True),
        sa.Column("integrity_ethics_question", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["job_requirements_id"], ["job_requirements.id"], ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assessments_id"), "assessments", ["id"], unique=False)

    op.create_table(
        "responses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("behavioral_response", sa.Text(), nullable=True),
        sa.Column("competency_based_response", sa.Text(), nullable=True),
        sa.Column("situational_response", sa.Text(), nullable=True),
        sa.Column("panel_response", sa.Text(), nullable=True),
        sa.Column("business_case_response", sa.Text(), nullable=True),
        sa.Column("live_simulation_response", sa.Text(), nullable=True),
        sa.Column("psychometric_response", sa.Text(), nullable=True),
        sa.Column("structured_reference_response", sa.Text(), nullable=True),
        sa.Column("culture_alignment_response", sa.Text(), nullable=True),
        sa.Column("integrity_ethics_response", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_responses_id"), "responses", ["id"], unique=False)

    op.create_table(
        "analysis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_requirements_id", sa.Integer(), nullable=False),
        sa.Column("assessment_id", sa.Integer(), nullable=False),
        sa.Column("responses_id", sa.Integer(), nullable=False),
        sa.Column("analysis", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["assessment_id"], ["assessments.id"], ),
        sa.ForeignKeyConstraint(["job_requirements_id"], ["job_requirements.id"], ),
        sa.ForeignKeyConstraint(["responses_id"], ["responses.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analysis_id"), "analysis", ["id"], unique=False)

    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("prediction", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analysis.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_predictions_id"), "predictions", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_predictions_id"), table_name="predictions")
    op.drop_table("predictions")
    op.drop_index(op.f("ix_analysis_id"), table_name="analysis")
    op.drop_table("analysis")
    op.drop_index(op.f("ix_responses_id"), table_name="responses")
    op.drop_table("responses")
    op.drop_index(op.f("ix_assessments_id"), table_name="assessments")
    op.drop_table("assessments")
    op.drop_index(op.f("ix_job_requirements_job_id"), table_name="job_requirements")
    op.drop_index(op.f("ix_job_requirements_id"), table_name="job_requirements")
    op.drop_table("job_requirements")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
