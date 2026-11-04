from alembic import op
import sqlalchemy as sa


revision = "0003_credentials"
down_revision = "0002_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("api_credentials",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("project_id", sa.String(100), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("revoked_at", sa.String(40), nullable=True),
        sa.CheckConstraint("(is_admin AND project_id IS NULL) OR (NOT is_admin AND project_id IS NOT NULL)"),
        sa.CheckConstraint("length(key_hash) = 64"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index("ix_api_credentials_project_id", "api_credentials", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_api_credentials_project_id", table_name="api_credentials")
    op.drop_table("api_credentials")
