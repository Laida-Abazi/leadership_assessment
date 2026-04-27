from typing import Sequence, Union
from alembic import op
from sqlalchemy.sql import func
from sqlalchemy import DateTime
import sqlalchemy as sa

revision: str = "014_add_created_at_to_models"
down_revision: Union[str, None] = "013_add_multi_assessment_tables"


def upgrade() -> None:
    op.add_column("users", sa.Column("created_at", DateTime(timezone=True), server_default=func.now()))
    op.add_column("assessments", sa.Column("created_at", DateTime(timezone=True), server_default=func.now()))
    op.add_column("responses", sa.Column("created_at", DateTime(timezone=True), server_default=func.now()))
    op.add_column("analysis", sa.Column("created_at", DateTime(timezone=True), server_default=func.now()))
    op.add_column("predictions", sa.Column("created_at", DateTime(timezone=True), server_default=func.now()))