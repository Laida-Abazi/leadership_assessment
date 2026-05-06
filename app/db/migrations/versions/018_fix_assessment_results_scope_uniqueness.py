"""Fix assessment_results uniqueness for candidate-scoped analysis.

Revision ID: 018_fix_result_uniqueness
Revises: 017_candidate_scoped_analysis
Create Date: 2026-05-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018_fix_result_uniqueness"
down_revision: Union[str, None] = "017_candidate_scoped_analysis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "assessment_results"
LEGACY_CONSTRAINT = "assessment_results_assessment_id_key"
LEGACY_INDEX = "ix_assessment_results_assessment_id"
NULL_SCOPE_INDEX = "uq_assessment_results_assessment_null_candidate"
CANDIDATE_SCOPE_INDEX = "uq_assessment_results_assessment_candidate"


def _unique_constraint_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {constraint["name"] for constraint in inspector.get_unique_constraints(TABLE_NAME)}


def _index_metadata() -> dict[str, dict]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"]: index for index in inspector.get_indexes(TABLE_NAME)}


def upgrade() -> None:
    unique_constraints = _unique_constraint_names()
    if LEGACY_CONSTRAINT in unique_constraints:
        op.drop_constraint(LEGACY_CONSTRAINT, TABLE_NAME, type_="unique")

    indexes = _index_metadata()
    legacy_index = indexes.get(LEGACY_INDEX)
    if legacy_index and legacy_index.get("unique"):
        op.drop_index(LEGACY_INDEX, table_name=TABLE_NAME)

    indexes = _index_metadata()
    if NULL_SCOPE_INDEX not in indexes:
        op.create_index(
            NULL_SCOPE_INDEX,
            TABLE_NAME,
            ["assessment_id"],
            unique=True,
            postgresql_where=sa.text("candidate_id IS NULL"),
        )
    if CANDIDATE_SCOPE_INDEX not in indexes:
        op.create_index(
            CANDIDATE_SCOPE_INDEX,
            TABLE_NAME,
            ["assessment_id", "candidate_id"],
            unique=True,
            postgresql_where=sa.text("candidate_id IS NOT NULL"),
        )


def downgrade() -> None:
    indexes = _index_metadata()
    if CANDIDATE_SCOPE_INDEX in indexes:
        op.drop_index(CANDIDATE_SCOPE_INDEX, table_name=TABLE_NAME)
    if NULL_SCOPE_INDEX in indexes:
        op.drop_index(NULL_SCOPE_INDEX, table_name=TABLE_NAME)

    unique_constraints = _unique_constraint_names()
    if LEGACY_CONSTRAINT not in unique_constraints:
        op.create_unique_constraint(LEGACY_CONSTRAINT, TABLE_NAME, ["assessment_id"])
