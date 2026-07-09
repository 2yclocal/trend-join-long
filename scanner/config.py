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
    max_results: int = 15          # cap the alert list

    # Trade plan
    stop_pct_below_pmh: float = 1.0   # stop = premarket high − 1%

    # Data fetching
    batch_size: int = 50           # symbols per yfinance batch download
    news_max_age_hours: int = 48   # catalyst headline freshness window

    # Premarket high robustness — drop 1-min bars whose upper wick exceeds
    # pm_wick_filter_k × the session's median bar range (bad ticks in Yahoo's
    # thin premarket feed). Scales with each stock's own volatility.
    # Raised 2.5->5.0 on 2026-07-09 after AMAT's real ~$2 premarket spike
    # (verified against TradingView) was incorrectly clipped at 2.5.
    pm_wick_filter_k: float = 5.0


settings = Settings()
