from alembic import op
import sqlalchemy as sa
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None
def upgrade():
    op.add_column('users',sa.Column('active',sa.Boolean(),nullable=False,server_default=sa.true()))
    op.add_column('challenges',sa.Column('status',sa.String(),nullable=False,server_default='published'))
    op.create_table('game_settings',sa.Column('slug',sa.String(),primary_key=True),sa.Column('name',sa.String(),nullable=False),sa.Column('description',sa.Text(),nullable=False),sa.Column('instructions',sa.Text(),nullable=False),sa.Column('available',sa.Boolean(),nullable=False),sa.Column('configuration',sa.Text(),nullable=False))
    op.create_table('audit_logs',sa.Column('id',sa.String(),primary_key=True),sa.Column('actor',sa.String(),nullable=False),sa.Column('action',sa.String(),nullable=False),sa.Column('target',sa.String(),nullable=False),sa.Column('details',sa.Text(),nullable=False),sa.Column('created',sa.Integer(),nullable=False))
    op.create_table('site_content',sa.Column('key',sa.String(),primary_key=True),sa.Column('value',sa.Text(),nullable=False))
def downgrade():
    op.drop_table('site_content');op.drop_table('audit_logs');op.drop_table('game_settings')
    with op.batch_alter_table('challenges') as batch:batch.drop_column('status')
    with op.batch_alter_table('users') as batch:batch.drop_column('active')
