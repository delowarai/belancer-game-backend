from alembic import op
import sqlalchemy as sa
revision='0001'
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('users',sa.Column('id',sa.String(),primary_key=True),sa.Column('username',sa.String(),unique=True,nullable=False),sa.Column('password',sa.String(),nullable=False),sa.Column('role',sa.String()))
    op.create_table('logins',sa.Column('token',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),nullable=False),sa.Column('expires',sa.Integer(),nullable=False))
    op.create_table('game_sessions',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),nullable=False),sa.Column('game',sa.String(),nullable=False),sa.Column('challenge',sa.String(),nullable=False),sa.Column('attempt',sa.Integer(),nullable=False),sa.Column('issued',sa.Integer(),nullable=False),sa.Column('payload',sa.Text(),nullable=False),sa.Column('result',sa.Text()),sa.UniqueConstraint('user_id','challenge','attempt'))
def downgrade():
    op.drop_table('game_sessions');op.drop_table('logins');op.drop_table('users')
