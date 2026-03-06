"""Add job_requirements fields: skill, soft_skill, experience, education, etc.

Revision ID: 002_job_req
Revises: 001_initial
Create Date: 2025-03-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_job_req"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job_requirements", sa.Column("skill", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("soft_skill", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("experience", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("education", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("certification", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("responsibility", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("language", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("industry_experience", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("role_experience", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("location", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("availability", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("work_authorization", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("seniority_level", sa.Text(), nullable=True))
    op.add_column("job_requirements", sa.Column("culture_fit", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_requirements", "culture_fit")
    op.drop_column("job_requirements", "seniority_level")
    op.drop_column("job_requirements", "work_authorization")
    op.drop_column("job_requirements", "availability")
    op.drop_column("job_requirements", "location")
    op.drop_column("job_requirements", "role_experience")
    op.drop_column("job_requirements", "industry_experience")
    op.drop_column("job_requirements", "language")
    op.drop_column("job_requirements", "responsibility")
    op.drop_column("job_requirements", "certification")
    op.drop_column("job_requirements", "education")
    op.drop_column("job_requirements", "experience")
    op.drop_column("job_requirements", "soft_skill")
    op.drop_column("job_requirements", "skill")
