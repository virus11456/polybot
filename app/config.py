from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Telegram
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Database
    database_url: str = "postgresql://roan:roan123@localhost:5432/roan"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Polymarket
    polymarket_api_base: str = "https://clob.polymarket.com"
    polymarket_gamma_base: str = "https://gamma-api.polymarket.com"

    # External data
    fred_api_key: str = ""

    # Trading mode: manual | semi | auto
    trading_mode: str = "manual"

    # Scanner settings
    scan_interval_seconds: int = 30
    min_profit_pct: float = 0.008  # 0.8% minimum profit
    min_liquidity: float = 1000.0
    fee_rate: float = 0.02  # 2% Polymarket fee

    # Capital management
    max_position_size: float = 5000.0
    default_position_size: float = 1000.0
    max_daily_exposure: float = 20000.0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
