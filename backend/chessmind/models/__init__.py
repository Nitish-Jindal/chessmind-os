"""SQLAlchemy ORM models.

Importing this package registers every model with ``Base.metadata`` so
Alembic autogenerate and test fixtures see the full schema.
"""

from chessmind.models.base import Base
from chessmind.models.player import Player

__all__ = ["Base", "Player"]
