"""initial empty schema

Revision ID: cda5b1882f0a
Revises:
Create Date: 2026-09-24 21:21:16.209253

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "cda5b1882f0a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
