"""Add fit_score, confidence_score, and risk_flags columns to predictions table.

Revision ID: 012_alter_predictions_add_fit_columns
Revises: 011_alter_analysis_add_jsonb_columns
Create Date: 2026-03-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012_add_pred_fit"
down_revision: Union[str, None] = "011_add_analysis_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("fit_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "predictions",
        sa.Column("confidence_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "predictions",
        sa.Column(
            "risk_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("predictions", "risk_flags")
    op.drop_column("predictions", "confidence_score")
    op.drop_column("predictions", "fit_score")
