from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "flagged_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("message_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("moderation_rules.id"), nullable=False),
        sa.Column("approved", sa.Boolean, nullable=False),
        sa.Column("moderator_id", sa.String(length=64), nullable=False),
        sa.Column("similarity", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("flagged_messages")
