from alembic import op
import sqlalchemy as sa
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None
def upgrade():
    # Older 0001 imports live metadata. It may already create this table on a
    # fresh database, whereas existing 0001 databases need it added here.
    if not sa.inspect(op.get_bind()).has_table('challenges'):
        op.create_table('challenges',sa.Column('id',sa.String(),primary_key=True),sa.Column('game',sa.String(),nullable=False),sa.Column('payload',sa.Text(),nullable=False))
def downgrade(): op.drop_table('challenges')
