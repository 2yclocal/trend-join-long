"""
yfinance data provider — batched daily bars, premarket 1-minute bars,
and news headlines (for the catalyst line).

Batched yf.download calls keep the request count low (~20 requests for
900 symbols) instead of one request per symbol.
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

from scanner.config import settings

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

_PM_START = dtime(4, 0)
_PM_END   = dtime(9, 30)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _per_symbol_frames(df: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Split a group_by='ticker' multi-symbol download into per-symbol frames."""
    out: dict[str, pd.DataFrame] = {}
    if df is None or df.empty:
        return out
    if isinstance(df.columns, pd.MultiIndex):
        for sym in symbols:
            if sym in df.columns.get_level_values(0):
                sub = df[sym].dropna(how="all")
                if not sub.empty:
                    out[sym] = sub
    elif len(symbols) == 1:
        out[symbols[0]] = df.dropna(how="all")
    return out


def get_daily_bars(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Last ~10 daily bars per symbol. Columns: Open/High/Low/Close/Volume."""
    result: dict[str, pd.DataFrame] = {}
    for chunk in _chunks(symbols, settings.batch_size):
        try:
            df = yf.download(
                chunk, period="10d", interval="1d",
                group_by="ticker", auto_adjust=False,
                threads=True, progress=False,
            )
            result.update(_per_symbol_frames(df, chunk))
        except Exception as exc:
            logger.warning(f"Daily download failed for chunk starting {chunk[0]}: {exc}")
    logger.info(f"Daily bars: {len(result)}/{len(symbols)} symbols")
    return result


def get_intraday_bars(symbols: list[str], days: int = 1) -> dict[str, pd.DataFrame]:
    """1-minute bars including pre/post market, index in ET."""
    result: dict[str, pd.DataFrame] = {}
    for chunk in _chunks(symbols, settings.batch_size):
        try:
            df = yf.download(
                chunk, period=f"{days}d", interval="1m",
                prepost=True, group_by="ticker", auto_adjust=False,
                threads=True, progress=False,
            )
            for sym, sub in _per_symbol_frames(df, chunk).items():
                sub = sub.copy()
                sub.index = pd.to_datetime(sub.index).tz_convert(ET)
                result[sym] = sub
        except Exception as exc:
            logger.warning(f"Intraday download failed for chunk starting {chunk[0]}: {exc}")
    logger.info(f"Intraday bars: {len(result)}/{len(symbols)} symbols")
    return result


def premarket_slice(df: pd.DataFrame, day) -> pd.DataFrame:
    """Rows in the 04:00–09:30 ET premarket window on the given ET date."""
    idx = df.index
    mask = (
        (pd.Index(idx.date) == day)
        & (pd.Index(idx.time) >= _PM_START)
        & (pd.Index(idx.time) < _PM_END)
    )
    return df[mask]


def _truncate(title: str) -> str:
    return title[:87] + "…" if len(title) > 90 else title


def _finnhub_headline(symbol: str) -> str:
    """Most recent company-news headline from Finnhub. '' if unavailable."""
    if not settings.finnhub_api_key:
        return ""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.news_max_age_hours)
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": symbol,
                "from": cutoff.date().isoformat(),
                "to": datetime.now(timezone.utc).date().isoformat(),
                "token": settings.finnhub_api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json() or []
    except Exception as exc:
        logger.warning(f"Finnhub news failed for {symbol}: {exc}")
        return ""

    best_title, best_ts = "", 0
    for it in items:
        ts = it.get("datetime") or 0
        title = (it.get("headline") or "").strip()
        if not title or ts <= best_ts:
            continue
        if datetime.fromtimestamp(ts, tz=timezone.utc) < cutoff:
            continue
        best_title, best_ts = title, ts
    return _truncate(best_title)


def get_catalyst(symbol: str) -> str:
    """Most recent news headline within the freshness window, one line.

    Finnhub first (if FINNHUB_API_KEY is set), Yahoo as fallback.
    """
    headline = _finnhub_headline(symbol)
    if headline:
        return headline

    try:
        items = yf.Ticker(symbol).news or []
    except Exception as exc:
        logger.warning(f"News fetch failed for {symbol}: {exc}")
        return ""

    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.news_max_age_hours)
    best_title, best_time = "", None

    for it in items:
        content = it.get("content", it)
        title = (content.get("title") or "").strip()
        if not title:
            continue

        published = None
        pub = content.get("pubDate") or content.get("displayTime")
        if pub:
            try:
                published = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
            except ValueError:
                pass
        elif it.get("providerPublishTime"):
            published = datetime.fromtimestamp(it["providerPublishTime"], tz=timezone.utc)

        if published is None or published < cutoff:
            continue
        if best_time is None or published > best_time:
            best_title, best_time = title, published

    return _truncate(best_title)
