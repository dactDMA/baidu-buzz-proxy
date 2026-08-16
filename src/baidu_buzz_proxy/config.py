from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="BBP_",
        extra="ignore",
    )

    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    redis_url: str = "redis://localhost:6379/0"
    baidu_pcs_go_path: Path = Path("./bin/BaiduPCS-Go")
    buzzheavier_access_token: SecretStr = SecretStr("")
    admin_access_token: SecretStr = SecretStr("")
    turnstile_site_key: str = ""
    turnstile_secret_key: SecretStr = SecretStr("")
    baidu_reserve_gib: int = Field(default=300, ge=0)
    max_active_jobs: int = Field(default=2, ge=1, le=32)
    job_page_ttl_days: int = Field(default=8, ge=1, le=365)
    failed_job_ttl_hours: int = Field(default=24, ge=1, le=168)
    stalled_job_timeout_hours: int = Field(default=24, ge=1, le=168)


@lru_cache
def get_settings() -> Settings:
    return Settings()
