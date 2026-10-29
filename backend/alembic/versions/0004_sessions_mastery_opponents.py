"""sessions, attempts, mastery, opponents, master move stats

Revision ID: 0004_sessions_mastery_opponents
Revises: 0003_engine_analysis
Create Date: 2026-10-29

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_sessions_mastery_opponents"
down_revision: Union[str, None] = "0003_engine_analysis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # opponent_profile
    op.create_table(
        "opponent_profile",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("player.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "aliases_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("games_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_import_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_opponent_profile_owner", "opponent_profile", ["owner_player_id"])

    # opponent_position_move
    op.create_table(
        "opponent_position_move",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "opponent_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opponent_profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("position.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("color", sa.String(length=5), nullable=False),
        sa.Column("move_uci", sa.String(length=10), nullable=False),
        sa.Column("games_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draws", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recency_weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("sample_size_bucket", sa.String(length=10), nullable=False, server_default="small"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("opponent_profile_id", "position_id", "color", "move_uci", name="uq_opponent_position_move"),
    )
    op.create_index("ix_opm_opponent", "opponent_position_move", ["opponent_profile_id"])
    op.create_index("ix_opm_position", "opponent_position_move", ["position_id"])

    # master_move_stat
    op.create_table(
        "master_move_stat",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("position.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="lichess_masters"),
        sa.Column("move_uci", sa.String(length=10), nullable=False),
        sa.Column("games_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("white_wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("black_wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draws", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("position_id", "source", "move_uci", name="uq_master_move_stat"),
    )
    op.create_index("ix_master_move_stat_position", "master_move_stat", ["position_id"])

    # training_session
    op.create_table(
        "training_session",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("player.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column(
            "opening_scope_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "opponent_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opponent_profile.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column(
            "settings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "state_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_training_session_player", "training_session", ["player_id"])

    # training_attempt
    op.create_table(
        "training_attempt",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("training_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("position.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("played_uci", sa.String(length=10), nullable=False),
        sa.Column(
            "best_uci_list_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("classification", sa.String(length=16), nullable=False),
        sa.Column("eval_before_cp", sa.Integer(), nullable=True),
        sa.Column("eval_after_cp", sa.Integer(), nullable=True),
        sa.Column("eval_swing_cp", sa.Integer(), nullable=True),
        sa.Column("mate_flip", sa.String(length=16), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hints_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visualization_score", sa.Float(), nullable=True),
        sa.Column(
            "feedback_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_training_attempt_session", "training_attempt", ["session_id"])
    op.create_index("ix_training_attempt_position", "training_attempt", ["position_id"])

    # mastery_state
    op.create_table(
        "mastery_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("player.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("position.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recall_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("understanding_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("visualization_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("speed_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="untested"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ease_factor", sa.Float(), nullable=False, server_default="2.5"),
        sa.Column("interval_days", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("player_id", "position_id", name="uq_mastery_player_position"),
    )
    op.create_index("ix_mastery_player", "mastery_state", ["player_id"])
    op.create_index("ix_mastery_due", "mastery_state", ["player_id", "due_at"])


def downgrade() -> None:
    op.drop_index("ix_mastery_due", table_name="mastery_state")
    op.drop_index("ix_mastery_player", table_name="mastery_state")
    op.drop_table("mastery_state")
    op.drop_index("ix_training_attempt_position", table_name="training_attempt")
    op.drop_index("ix_training_attempt_session", table_name="training_attempt")
    op.drop_table("training_attempt")
    op.drop_index("ix_training_session_player", table_name="training_session")
    op.drop_table("training_session")
    op.drop_index("ix_master_move_stat_position", table_name="master_move_stat")
    op.drop_table("master_move_stat")
    op.drop_index("ix_opm_position", table_name="opponent_position_move")
    op.drop_index("ix_opm_opponent", table_name="opponent_position_move")
    op.drop_table("opponent_position_move")
    op.drop_index("ix_opponent_profile_owner", table_name="opponent_profile")
    op.drop_table("opponent_profile")
