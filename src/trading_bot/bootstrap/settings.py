from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading_bot.domain.enums import Environment


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


class BootstrapSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TB_",
        case_sensitive=False,
        extra="ignore",
    )

    env: Environment
    config_file: str
    postgres_dsn: str = Field(min_length=1)
    redis_dsn: str = Field(min_length=1)
    log_level: str = "INFO"
    http_host: str = "0.0.0.0"
    http_port: int = 8080

    @property
    def resolved_config_file(self) -> Path:
        candidate = Path(self.config_file)
        return candidate if candidate.is_absolute() else project_root() / candidate
