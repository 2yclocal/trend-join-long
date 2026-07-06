from pydantic_settings import BaseSettings, SettingsConfigDict
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Finnhub (better news headlines; falls back to Yahoo if unset)
    finnhub_api_key: str = ""

    # Gap scan filters
    gap_min_pct: float = 3.0       # minimum gap % vs previous close
    min_price: float = 3.0         # minimum share price
    max_results: int = 20          # cap the alert list

    # Trade plan
    stop_pct_below_pmh: float = 1.0   # stop = premarket high − 1%

    # Data fetching
    batch_size: int = 50           # symbols per yfinance batch download
    news_max_age_hours: int = 48   # catalyst headline freshness window


settings = Settings()
