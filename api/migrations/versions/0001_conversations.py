from alembic import op
import sqlalchemy as sa


revision = '0001_conversations'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('conversations',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.String(length=40), nullable=False),
        sa.Column('updated_at', sa.String(length=40), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('message_sequence', sa.Integer(), nullable=False),
        sa.Column('preferences', sa.JSON(), nullable=False),
        sa.Column('stable_instructions', sa.JSON(), nullable=False),
        sa.Column('pending', sa.JSON(), nullable=True),
        sa.Column('processing_id', sa.String(length=36), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_created_at'), 'conversations', ['created_at'], unique=False)
    op.create_table('generated_entries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('current_version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'accepted', 'rejected')"),
        sa.CheckConstraint('current_version >= 1'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'position')
    )
    op.create_index(op.f('ix_generated_entries_conversation_id'), 'generated_entries', ['conversation_id'], unique=False)
    op.create_table('reference_material',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('import_status', sa.String(length=20), nullable=True),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.CheckConstraint("kind IN ('examples', 'headlines', 'urls')"),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reference_material_conversation_id'), 'reference_material', ['conversation_id'], unique=False)
    op.create_table('submissions',
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('operation', sa.String(length=20), nullable=False),
        sa.Column('request_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('response', sa.JSON(), nullable=True),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.String(length=40), nullable=False),
        sa.Column('expires_at', sa.Double(), nullable=False),
        sa.CheckConstraint("operation IN ('message', 'import')"),
        sa.CheckConstraint("status IN ('processing', 'completed')"),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
        sa.PrimaryKeyConstraint('conversation_id', 'id')
    )
    op.create_index(op.f('ix_submissions_expires_at'), 'submissions', ['expires_at'], unique=False)
    op.create_table('chat_messages',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('submission_id', sa.String(length=36), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=10), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.String(length=40), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')"),
        sa.ForeignKeyConstraint(['conversation_id', 'submission_id'], ['submissions.conversation_id', 'submissions.id'], ),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'sequence')
    )
    op.create_index(op.f('ix_chat_messages_conversation_id'), 'chat_messages', ['conversation_id'], unique=False)
    op.create_table('entry_versions',
        sa.Column('entry_id', sa.String(length=36), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.String(length=40), nullable=False),
        sa.CheckConstraint('version >= 1'),
        sa.ForeignKeyConstraint(['entry_id'], ['generated_entries.id'], ),
        sa.PrimaryKeyConstraint('entry_id', 'version')
    )
    op.create_table('entry_reviews',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('entry_id', sa.String(length=36), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.String(length=40), nullable=False),
        sa.CheckConstraint("status IN ('accepted', 'rejected')"),
        sa.ForeignKeyConstraint(['entry_id', 'version'], ['entry_versions.entry_id', 'entry_versions.version'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_entry_reviews_entry_id'), 'entry_reviews', ['entry_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_entry_reviews_entry_id'), table_name='entry_reviews')
    op.drop_table('entry_reviews')
    op.drop_table('entry_versions')
    op.drop_index(op.f('ix_chat_messages_conversation_id'), table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index(op.f('ix_submissions_expires_at'), table_name='submissions')
    op.drop_table('submissions')
    op.drop_index(op.f('ix_reference_material_conversation_id'), table_name='reference_material')
    op.drop_table('reference_material')
    op.drop_index(op.f('ix_generated_entries_conversation_id'), table_name='generated_entries')
    op.drop_table('generated_entries')
    op.drop_index(op.f('ix_conversations_created_at'), table_name='conversations')
    op.drop_table('conversations')
