"""Settings loaded from environment variables and an optional .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["live", "record", "replay"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    qloo_api_key: SecretStr = SecretStr("")
    qloo_base_url: str = "https://hackathon.api.qloo.com"
    qloo_mode: Mode = "live"
    qloo_enabled: bool = True

    # Budgets count live HTTP attempts (retries included).
    qloo_max_live_calls: int = 600  # lifetime cap for this deployment's call log
    qloo_daily_live_cap: int = 500
    qloo_client_hourly_cap: int = 60
    qloo_run_cap: int = 150
    qloo_demo_reserve: int = 100
    qloo_rps: float = 4.0  # Qloo allows 5 req/s (VERIFIED header); keep headroom

    qloo_cache_path: Path = Path("data/qloo_cache.sqlite")
    qloo_cache_ttl_days: float = 7.0
    qloo_snapshot_path: Path = Path("data/snapshot/snapshot.json")
    qloo_raw_dir: Path = Path("data/raw")
    groundtruth_path: Path = Path("data/groundtruth.sqlite")  # answer keys (#18); holds Qloo names

    qloo_timeout_s: float = 10.0
    qloo_max_concurrency: int = 4
    qloo_max_take: int = 50
    qloo_max_param_chars: int = 2000
    qloo_max_attempts: int = 4
    qloo_breaker_threshold: int = 3
    qloo_breaker_cooldown_s: float = 30.0

    ip_hash_salt: SecretStr = SecretStr("")
    allowed_origins: str = ""
    # Number of trusted reverse proxies in front of the app (Render = 1). 0 = ignore X-Forwarded-For.
    trusted_proxy_hops: int = 0
    http_rate_per_minute: int = 30
    # Overall deadline for one web request (resolution + retries + backoff + Qloo calls).
    request_deadline_s: float = 8.0
    config_dir: Path = Path("config")

    @field_validator("qloo_base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def has_key(self) -> bool:
        return bool(self.qloo_api_key.get_secret_value().strip())

    @property
    def effective_mode(self) -> Mode:
        """Live/record without a key cannot work, so fall back to replay."""
        if self.qloo_mode != "replay" and not self.has_key:
            return "replay"
        return self.qloo_mode

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def adhoc_daily_cap(self) -> int:
        """Daily calls available to non-demo traffic; the rest is reserved for demo calls."""
        return max(0, self.qloo_daily_live_cap - self.qloo_demo_reserve)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def settings_for_tests(**overrides: object) -> Settings:
    """Build settings that ignore the developer's .env file."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


__all__ = ["Field", "Mode", "Settings", "get_settings", "settings_for_tests"]
