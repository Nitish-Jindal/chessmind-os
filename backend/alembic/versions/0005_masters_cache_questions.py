"""lichess masters explorer cache + session position questions

Revision ID: 0005_masters_cache_questions
Revises: 0004_sessions_mastery_opponents
Create Date: 2026-10-30

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_masters_cache_questions"
down_revision: Union[str, None] = "0004_sessions_mastery_opponents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "master_explorer_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_key", sa.Text(), nullable=False, unique=True),
        sa.Column("fen", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="lichess_masters"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_games", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "position_question",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("training_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fen", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("expected_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("answer_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_position_question_session_id", "position_question", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_position_question_session_id", table_name="position_question")
    op.drop_table("position_question")
    op.drop_table("master_explorer_cache")
