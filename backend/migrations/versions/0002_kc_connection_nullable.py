"""knowledge_collections.connection_id nullable (BE1, integration fix I-1)

Revision ID: 0002_kc_connection_nullable
Revises: 0001_initial
Create Date: 2026-11-26 08:00:00

`DELETE /connections/{id}?force=true` unlinks knowledge collections (connection_id → NULL, status → failed).
SQL Server cannot ALTER COLUMN a column referenced by a FOREIGN KEY constraint, so the FK (and its index) are
dropped and recreated around the change — portable on PostgreSQL too. Downgrade requires that no row has a NULL
connection_id (re-link or delete such collections first); it fails loudly otherwise.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002_kc_connection_nullable'
down_revision: Union[str, Sequence[str], None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'knowledge_collections'
FK = 'fk_knowledge_collections_connection_id'
IX = 'ix_knowledge_collections_connection_id'


def _alter(nullable: bool) -> None:
    op.drop_constraint(FK, TABLE, type_='foreignkey')
    op.drop_index(IX, table_name=TABLE)
    op.alter_column(TABLE, 'connection_id', existing_type=sa.String(length=40), nullable=nullable)
    op.create_index(IX, TABLE, ['connection_id'], unique=False)
    op.create_foreign_key(FK, TABLE, 'connections', ['connection_id'], ['id'])


def upgrade() -> None:
    _alter(True)


def downgrade() -> None:
    _alter(False)
