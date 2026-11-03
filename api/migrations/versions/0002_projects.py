from alembic import op
import sqlalchemy as sa


revision = '0002_projects'
down_revision = '0001_conversations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('projects',
        sa.Column('project_id', sa.String(length=100), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.String(length=40), nullable=False),
        sa.Column('updated_at', sa.String(length=40), nullable=False),
        sa.Column('generation_settings', sa.JSON(), nullable=False),
        sa.Column('instructions', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('project_id')
    )
    op.create_table('project_references',
        sa.Column('project_id', sa.String(length=100), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('import_status', sa.String(length=20), nullable=True),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.CheckConstraint("kind IN ('examples', 'headlines', 'urls')"),
        sa.CheckConstraint('position >= 0'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.project_id'], ),
        sa.PrimaryKeyConstraint('project_id', 'id'),
        sa.UniqueConstraint('project_id', 'kind', 'position')
    )


def downgrade() -> None:
    op.drop_table('project_references')
    op.drop_table('projects')
