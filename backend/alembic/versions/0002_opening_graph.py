"""opening graph + import pipeline tables

Revision ID: 0002_opening_graph
Revises: 0001_player
Create Date: 2026-10-29

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_opening_graph"
down_revision: Union[str, None] = "0001_player"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("player.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("opponent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="upload"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("progress_games", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_games", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "artifacts_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_import_job_player_id", "import_job", ["player_id"])

    op.create_table(
        "game",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("player.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="upload"),
        sa.Column("pgn", sa.Text(), nullable=False),
        sa.Column(
            "imported_from_batch",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("import_job.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("white_name", sa.String(length=200), nullable=False, server_default="?"),
        sa.Column("black_name", sa.String(length=200), nullable=False, server_default="?"),
        sa.Column("white_rating", sa.Integer(), nullable=True),
        sa.Column("black_rating", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(length=8), nullable=False, server_default="*"),
        sa.Column("date_played", sa.Date(), nullable=True),
        sa.Column("time_control", sa.String(length=32), nullable=True),
        sa.Column("eco", sa.String(length=5), nullable=True),
        sa.Column(
            "provenance_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_game_player_id", "game", ["player_id"])

    op.create_table(
        "position",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_key", sa.Text(), nullable=False),
        sa.Column("fen", sa.Text(), nullable=False),
        sa.Column("eco", sa.String(length=5), nullable=True),
        sa.Column("opening_name", sa.String(length=200), nullable=True),
        sa.Column(
            "features_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("popularity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_position_canonical_key", "position", ["canonical_key"], unique=True)

    op.create_table(
        "move_edge",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "from_position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("position.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("position.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("uci", sa.String(length=10), nullable=False),
        sa.Column("san", sa.String(length=10), nullable=False),
        sa.Column("games_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("white_wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("black_wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draws", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("from_position_id", "uci", name="uq_move_edge_from_uci"),
    )
    op.create_index("ix_move_edge_from_position_id", "move_edge", ["from_position_id"])

    op.create_table(
        "repertoire_line",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("player.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("color", sa.String(length=5), nullable=False),
        sa.Column(
            "from_position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("position.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("preferred_uci", sa.String(length=10), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_mainline", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "source_game_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("game.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_variation_path", sa.Text(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column(
            "nags_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "player_id", "color", "from_position_id", "preferred_uci",
            name="uq_repertoire_line_player_color_from_uci",
        ),
    )
    op.create_index("ix_repertoire_line_player_id", "repertoire_line", ["player_id"])
    op.create_index("ix_repertoire_line_from_position_id", "repertoire_line", ["from_position_id"])


def downgrade() -> None:
    op.drop_index("ix_repertoire_line_from_position_id", table_name="repertoire_line")
    op.drop_index("ix_repertoire_line_player_id", table_name="repertoire_line")
    op.drop_table("repertoire_line")
    op.drop_index("ix_move_edge_from_position_id", table_name="move_edge")
    op.drop_table("move_edge")
    op.drop_index("ix_position_canonical_key", table_name="position")
    op.drop_table("position")
    op.drop_index("ix_game_player_id", table_name="game")
    op.drop_table("game")
    op.drop_index("ix_import_job_player_id", table_name="import_job")
    op.drop_table("import_job")
