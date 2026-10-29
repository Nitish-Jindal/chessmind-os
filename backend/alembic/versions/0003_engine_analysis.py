"""engine_analysis cache table

Revision ID: 0003_engine_analysis
Revises: 0002_opening_graph
Create Date: 2026-10-29

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_engine_analysis"
down_revision: Union[str, None] = "0002_opening_graph"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "engine_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("position.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("engine", sa.String(length=32), nullable=False, server_default="stockfish"),
        sa.Column("engine_version", sa.String(length=32), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("multipv", sa.Integer(), nullable=False),
        sa.Column("nodes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("time_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("perspective", sa.String(length=5), nullable=False, server_default="white"),
        sa.Column(
            "results_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "cached_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_engine_analysis_position_id", "engine_analysis", ["position_id"])
    op.create_index(
        "ix_engine_analysis_pos_version_depth",
        "engine_analysis",
        ["position_id", "engine_version", "depth"],
    )


def downgrade() -> None:
    op.drop_index("ix_engine_analysis_pos_version_depth", table_name="engine_analysis")
    op.drop_index("ix_engine_analysis_position_id", table_name="engine_analysis")
    op.drop_table("engine_analysis")
