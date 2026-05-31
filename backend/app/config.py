from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Senyao's LLM Benchmark"
    database_url: str = "sqlite:///./data/benchmark.db"
    tasks_dir: Path = Path("tasks")
    app_secret_key: str = "dev-only-change-me"
    cors_origins: str = "*"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
