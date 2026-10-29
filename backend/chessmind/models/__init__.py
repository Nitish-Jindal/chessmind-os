"""SQLAlchemy ORM models.

Importing this package registers every model with ``Base.metadata`` so
Alembic autogenerate and test fixtures see the full schema.
"""

from chessmind.models.base import Base
from chessmind.models.engine_analysis import EngineAnalysis
from chessmind.models.game import Game, ImportJob, RepertoireLine
from chessmind.models.opponent import MasterMoveStat, OpponentPositionMove, OpponentProfile
from chessmind.models.player import Player
from chessmind.models.position import MoveEdge, Position
from chessmind.models.session import MasteryState, TrainingAttempt, TrainingSession

__all__ = [
    "Base",
    "EngineAnalysis",
    "Game",
    "ImportJob",
    "MasterMoveStat",
    "MasteryState",
    "MoveEdge",
    "OpponentPositionMove",
    "OpponentProfile",
    "Player",
    "Position",
    "RepertoireLine",
    "TrainingAttempt",
    "TrainingSession",
]
