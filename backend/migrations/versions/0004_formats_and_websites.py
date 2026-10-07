"""Website sources, page/slide locators, source URIs, and the crawl job kind."""

import sqlalchemy as sa
from alembic import op

revision = "0004_formats_and_websites"
down_revision = "0003_consulted_documents"
branch_labels = None
depends_on = None

OLD_KINDS = "'document.index_requested', 'document.delete_requested', 'source.delete_requested'"
NEW_KINDS = f"{OLD_KINDS}, 'source.sync_requested'"
DOWNGRADE_BLOCKERS = """
SELECT EXISTS (SELECT 1 FROM sources WHERE kind = 'website')
    OR EXISTS (
        SELECT 1 FROM documents WHERE length(external_id) > 200 OR length(media_type) > 50
    )
"""


def upgrade():
    op.add_column("sources", sa.Column("url", sa.Text()))
    _replace_check("sources", "ck_source_kind", "kind IN ('upload', 'website')")
    op.alter_column("documents", "external_id", type_=sa.Text(), existing_nullable=False)
    op.alter_column("documents", "media_type", type_=sa.String(100), existing_nullable=False)
    op.add_column("documents", sa.Column("source_uri", sa.Text()))
    op.add_column("chunks", sa.Column("page", sa.Integer()))
    op.add_column("chunks", sa.Column("slide", sa.Integer()))
    op.add_column("citations", sa.Column("source_url", sa.Text()))
    _replace_check("jobs", "ck_job_kind", f"kind IN ({NEW_KINDS})")


def downgrade():
    """Refuses rather than silently dropping data the older schema cannot hold."""
    if op.get_bind().execute(sa.text(DOWNGRADE_BLOCKERS)).scalar():
        raise RuntimeError(
            "Delete website sources and documents with long names or new file types "
            "(Word, PowerPoint, Excel) before downgrading below 0004."
        )
    op.execute("DELETE FROM jobs WHERE kind = 'source.sync_requested'")
    _replace_check("jobs", "ck_job_kind", f"kind IN ({OLD_KINDS})")
    op.drop_column("citations", "source_url")
    op.drop_column("chunks", "slide")
    op.drop_column("chunks", "page")
    op.drop_column("documents", "source_uri")
    op.alter_column("documents", "media_type", type_=sa.String(50), existing_nullable=False)
    op.alter_column("documents", "external_id", type_=sa.String(200), existing_nullable=False)
    _replace_check("sources", "ck_source_kind", "kind IN ('upload')")
    op.drop_column("sources", "url")


def _replace_check(table: str, name: str, condition: str):
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, condition)
