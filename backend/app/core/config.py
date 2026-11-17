from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """从项目根目录 .env 和进程环境读取配置；不主动调用外部服务。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    bosshunter_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8687")
    chrome_cdp_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:9222")
    collector_daily_limit: int = Field(default=20, ge=1)
    backend_host: Literal["127.0.0.1"] = "127.0.0.1"
    backend_port: int = Field(default=8200, ge=1, le=65535)
    database_url: str = "sqlite:///./data/jobpilot.db"
    ai_base_url: str = ""
    ai_api_key: SecretStr = SecretStr("")
    ai_model: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
