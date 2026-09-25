"""vault notes

Revision ID: bf32cbb696fe
Revises: 6bb5fb945a69
Create Date: 2026-09-25 16:52:34.844227

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bf32cbb696fe"
down_revision: str | Sequence[str] | None = "6bb5fb945a69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("source_link", schema=None) as batch_op:
        batch_op.alter_column("calendar_url", new_column_name="container")
        batch_op.alter_column("calendar_name", new_column_name="container_name")

    op.create_table(
        "note_ref",
        sa.Column("vault_path", sa.String(length=1000), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("modified_at", sa.DateTime(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channel.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vault_path"),
    )
    with op.batch_alter_table("note_ref", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_note_ref_channel_id"), ["channel_id"], unique=False)

    # Full-text index of note bodies (not a model: SQLAlchemy has no FTS5 type). The
    # trigram tokenizer matches inside Korean words, e.g. "알고리즘" in "알고리즘의".
    op.execute(
        "CREATE VIRTUAL TABLE note_fts USING fts5("
        "note_id UNINDEXED, title, body, tokenize='trigram')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE note_fts")
    with op.batch_alter_table("note_ref", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_note_ref_channel_id"))
    op.drop_table("note_ref")
    with op.batch_alter_table("source_link", schema=None) as batch_op:
        batch_op.alter_column("container", new_column_name="calendar_url")
        batch_op.alter_column("container_name", new_column_name="calendar_name")
