import logging
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("aeroguard")

DEV_KEY_PLACEHOLDER = "change-me-to-a-long-random-value"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AEROGUARD_", env_file=".env", extra="ignore"
    )

    api_key: str = ""
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: str = "data/aeroguard.db"
    # High enough for live webcam pose streaming (~8 fps ≈ 480 req/min)
    # plus normal console traffic; still bounds abuse on the LAN.
    rate_limit_per_minute: int = 900
    log_level: str = "INFO"
    max_alerts_in_memory: int = 500
    confidence_threshold: float = 0.5

    def resolve_api_key(self) -> str:
        if not self.api_key or self.api_key == DEV_KEY_PLACEHOLDER:
            generated = secrets.token_urlsafe(24)
            logger.warning(
                "AEROGUARD_API_KEY not set — generated ephemeral dev key: %s",
                generated,
            )
            self.api_key = generated
        return self.api_key


settings = Settings()
