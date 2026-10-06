from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DRY_RUN_BOT_TOKEN = "123456789:DRY-RUN-TOKEN"


class ConfigurationError(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    bot_token: str = ""
    dry_run: bool = False
    dev_endpoints: bool | None = None

    database_url: str = "sqlite+aiosqlite:///./data/cane.db"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"

    hmac_required: bool = True
    default_period_s: int = Field(default=30, ge=1)
    max_body_bytes: int = Field(default=4096, ge=256)
    batch_max_packets: int = Field(default=50, ge=1)

    silence_periods: int = Field(default=3, ge=1)
    monitor_interval_s: float = Field(default=15, gt=0)
    outbox_poll_s: float = Field(default=2, gt=0)
    outbox_max_attempts: int = Field(default=8, ge=1)
    locate_timeout_s: int = Field(default=300, ge=10)

    sos_live_min: int = Field(default=10, ge=1, le=1440)
    sos_remind_s: int = Field(default=120, ge=10)
    sos_remind_max: int = Field(default=3, ge=0)
    buffered_alert_max_age_min: int = Field(default=30, ge=0)

    cooldown_still_min: int = Field(default=10, ge=0)
    cooldown_batt_low_min: int = Field(default=360, ge=0)
    cooldown_batt_crit_min: int = Field(default=60, ge=0)
    cooldown_gps_min: int = Field(default=10, ge=0)
    still_default_min: int = Field(default=10, ge=1)

    bind_max_failures: int = Field(default=5, ge=1)
    bind_block_min: int = Field(default=60, ge=1)
    invite_ttl_h: int = Field(default=24, ge=1)
    history_limit: int = Field(default=10, ge=1, le=50)

    display_tz: str = "Europe/Moscow"
    map_url_template: str = "https://yandex.ru/maps/?pt={lon},{lat}&z=17&l=map"

    @property
    def dev_endpoints_enabled(self) -> bool:
        if self.dev_endpoints is None:
            return self.dry_run
        return self.dev_endpoints

    def effective_bot_token(self) -> str:
        if self.dry_run:
            return self.bot_token or DRY_RUN_BOT_TOKEN
        if not self.bot_token:
            raise ConfigurationError(
                "BOT_TOKEN не задан. Укажите токен от @BotFather или включите DRY_RUN=true."
            )
        return self.bot_token
