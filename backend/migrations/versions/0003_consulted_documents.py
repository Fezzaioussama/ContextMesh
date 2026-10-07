"""Record every document an answer consulted, so deletion can withhold it."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_consulted_documents"
down_revision = "0002_knowledge"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "answers",
        sa.Column(
            "consulted_document_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade():
    op.drop_column("answers", "consulted_document_ids")
