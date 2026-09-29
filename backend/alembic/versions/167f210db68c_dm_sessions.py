"""dm sessions

Revision ID: 167f210db68c
Revises: dc8fa5634cf8
Create Date: 2026-09-28 19:06:07.081278

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "167f210db68c"
down_revision: str | Sequence[str] | None = "dc8fa5634cf8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "dm_session",
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=True),
        sa.Column("last_active_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["channel.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("dm_session", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_dm_session_channel_id"), ["channel_id"], unique=False)

    # A plain ADD COLUMN ... REFERENCES: recreating `message` (batch mode) fails while
    # other tables' foreign keys point at it.
    op.execute("ALTER TABLE message ADD COLUMN session_id VARCHAR(36) REFERENCES dm_session (id)")
    op.create_index(op.f("ix_message_session_id"), "message", ["session_id"], unique=False)

    # Each existing DM keeps its history as one conversation.
    conn = op.get_bind()
    channels = conn.execute(
        sa.text(
            "SELECT c.id, MIN(m.created_at), MAX(m.created_at) FROM channel c"
            " JOIN message m ON m.channel_id = c.id AND m.thread_root_id IS NULL"
            " WHERE c.kind = 'dm' GROUP BY c.id"
        )
    ).all()
    for channel_id, first_at, last_at in channels:
        first_user = conn.execute(
            sa.text(
                "SELECT body FROM message WHERE channel_id = :c AND thread_root_id IS NULL"
                " AND author_type = 'user' ORDER BY created_at LIMIT 1"
            ),
            {"c": channel_id},
        ).scalar()
        title = (
            first_user.strip().splitlines()[0][:100] if first_user and first_user.strip() else None
        )
        session_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO dm_session (id, channel_id, title, last_active_at, created_at,"
                " updated_at) VALUES (:id, :c, :title, :last, :first, :last)"
            ),
            {"id": session_id, "c": channel_id, "title": title, "first": first_at, "last": last_at},
        )
        conn.execute(
            sa.text(
                "UPDATE message SET session_id = :s WHERE channel_id = :c"
                " AND thread_root_id IS NULL"
            ),
            {"s": session_id, "c": channel_id},
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_message_session_id"), table_name="message")
    op.execute("ALTER TABLE message DROP COLUMN session_id")

    with op.batch_alter_table("dm_session", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dm_session_channel_id"))

    op.drop_table("dm_session")
