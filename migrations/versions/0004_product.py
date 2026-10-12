"""Unified catalog policies, premium entitlements, orders and practice activity."""
from alembic import op
import sqlalchemy as sa
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('practice_saves',sa.Column('user_id',sa.String(),primary_key=True),sa.Column('game',sa.String(),primary_key=True),sa.Column('data',sa.Text(),nullable=False),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('updated',sa.Integer(),nullable=False))
    op.create_table('game_policies',sa.Column('slug',sa.String(),primary_key=True),sa.Column('access',sa.String(),nullable=False,server_default='free'),sa.Column('price_bdt',sa.Integer(),nullable=False,server_default='0'),sa.Column('price_usd',sa.Integer(),nullable=False,server_default='0'),sa.Column('version',sa.Integer(),nullable=False,server_default='1'))
    op.create_table('entitlements',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),nullable=False),sa.Column('game',sa.String(),nullable=False),sa.Column('expires',sa.Integer()),sa.Column('source',sa.String(),nullable=False),sa.Column('revoked',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_index('ix_entitlements_user_id','entitlements',['user_id'])
    op.create_table('practice_history',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),nullable=False),sa.Column('game',sa.String(),nullable=False),sa.Column('run_id',sa.String(),nullable=False),sa.Column('outcome',sa.String(),nullable=False),sa.Column('created',sa.Integer(),nullable=False),sa.UniqueConstraint('user_id','run_id'))
    op.create_index('ix_practice_history_user_id','practice_history',['user_id'])
    op.create_table('orders',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),nullable=False),sa.Column('request_key',sa.String(),nullable=False),sa.Column('product',sa.String(),nullable=False),sa.Column('provider',sa.String(),nullable=False),sa.Column('amount',sa.Integer(),nullable=False),sa.Column('currency',sa.String(),nullable=False),sa.Column('days',sa.Integer(),nullable=False,server_default='0'),sa.Column('status',sa.String(),nullable=False),sa.Column('sandbox',sa.Boolean(),nullable=False),sa.Column('provider_session',sa.String()),sa.Column('validation_id',sa.String(),unique=True),sa.Column('checkout_url',sa.Text()),sa.Column('created',sa.Integer(),nullable=False),sa.Column('paid',sa.Integer()),sa.UniqueConstraint('user_id','request_key'))
    op.create_index('ix_orders_user_id','orders',['user_id'])

def downgrade():
    op.drop_table('practice_saves')
    op.drop_table('orders');op.drop_table('practice_history');op.drop_table('entitlements');op.drop_table('game_policies')
