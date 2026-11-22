"""A download made for a transfer names it in a column of its own (`download_items.import_id`), not only inside its
query: the transfer's counts and activity are read by it on every look at the transfers.

Revision ID: 30a000000003
Revises: 30a000000002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '30a000000003'
down_revision: Union[str, None] = '30a000000002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('download_items', sa.Column('import_id', sa.Integer(), nullable=True))
    op.execute("""
        UPDATE download_items d SET import_id = (d.query_json->'import'->>'id')::int
        WHERE d.query_json ? 'import'
          AND EXISTS (SELECT 1 FROM imports i WHERE i.id = (d.query_json->'import'->>'id')::int)
    """)
    op.create_foreign_key('download_items_import_id_fkey', 'download_items', 'imports', ['import_id'], ['id'],
                          ondelete='SET NULL')
    op.create_index(op.f('ix_download_items_import_id'), 'download_items', ['import_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_download_items_import_id'), table_name='download_items')
    op.drop_constraint('download_items_import_id_fkey', 'download_items', type_='foreignkey')
    op.drop_column('download_items', 'import_id')
