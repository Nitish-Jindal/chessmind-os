"""Runtime configuration pulled from the environment."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://chessmind:chessmind@localhost:5432/chessmind",
        description="SQLAlchemy async URL. Must be Postgres; SQLite is unsupported.",
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg://chessmind:chessmind@localhost:5432/chessmind",
        description="Sync URL used only by Alembic.",
    )
    jwt_secret: str = Field(default="dev-only-not-for-prod")
    jwt_access_ttl_minutes: int = 60
    jwt_refresh_ttl_days: int = 14
    stockfish_path: str = Field(
        default="../engines/stockfish-bin",
        description="Absolute or working-dir-relative path to the Stockfish UCI binary.",
    )
    stockfish_default_depth: int = 18
    stockfish_default_multipv: int = 3
    environment: str = Field(default="dev")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="Comma-separated browser origins allowed to call the API (the Vite frontend).",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
