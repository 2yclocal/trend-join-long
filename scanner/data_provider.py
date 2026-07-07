"""
yfinance data provider — batched daily bars, premarket 1-minute bars,
and news headlines (for the catalyst line).

Batched yf.download calls keep the request count low (~20 requests for
900 symbols) instead of one request per symbol.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
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


# Headlines from stock-roundup bots (ChartMill, generic wires) that name a
# basket rather than a catalyst. Skipped in favour of company-specific news.
_GENERIC_MARKERS = (
    "gainers and losers", "top gainers", "top losers", "top movers",
    "biggest movers", "market movers", "movers within", "stocks moving",
    "stocks are moving", "moving in today", "stocks gapping",
    "stocks are gapping", "stocks that are", "stocks to watch",
    "pre-market session", "premarket session", "sector update",
    "stock market today", "dow jones futures", "s&p500 index", "s&p 500 index",
)

# Corporate-name noise words stripped before matching a headline to a company.
_NAME_STOPWORDS = {
    "the", "corporation", "corp", "inc", "incorporated", "company", "co",
    "technologies", "technology", "ltd", "limited", "group", "holdings",
    "plc", "international", "industries", "systems", "class", "&",
}


@dataclass
class _News:
    title: str
    summary: str
    ts: int   # epoch seconds


def _cutoff_ts() -> int:
    return int((datetime.now(timezone.utc)
                - timedelta(hours=settings.news_max_age_hours)).timestamp())


def _is_generic(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _GENERIC_MARKERS)


def _name_tokens(company_name: str) -> list[str]:
    toks = []
    for w in company_name.replace(",", " ").replace(".", " ").split():
        wl = w.lower().strip("&")
        if len(wl) >= 3 and wl not in _NAME_STOPWORDS:
            toks.append(wl)
    return toks


def _relevance(item: _News, symbol: str, name_tokens: list[str]) -> int:
    hay = item.title.lower()
    score = 0
    root = symbol.split(".")[0].lower()   # strip .TO etc.
    if re.search(rf"\b{re.escape(root)}\b", hay):
        score += 3
    if any(tok in hay for tok in name_tokens):
        score += 2
    return score


def _clip(text: str, limit: int) -> str:
    """Collapse whitespace and cut at a word boundary with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _first_sentence(summary: str, limit: int = 160) -> str:
    s = " ".join(summary.split())
    m = re.search(r"(.+?[.!?])(\s|$)", s)
    return _clip(m.group(1) if m else s, limit)


def _select_catalyst(items: list[_News], symbol: str, company_name: str) -> tuple[str, str]:
    """Pick the most relevant fresh headline; return (headline, summary_sentence)."""
    cut = _cutoff_ts()
    fresh = [it for it in items if it.title and it.ts >= cut]
    if not fresh:
        return "", ""

    # Prefer company-specific headlines; fall back to generic only if nothing else.
    specific = [it for it in fresh if not _is_generic(it.title)]
    pool = specific or fresh
    pool.sort(key=lambda it: (_relevance(it, symbol, _name_tokens(company_name)), it.ts),
              reverse=True)
    best = pool[0]

    headline = _clip(best.title, 110)
    summary = "" if (not best.summary or _is_generic(best.summary)) \
        else _first_sentence(best.summary, 160)
    # Drop a summary that just restates the headline.
    if summary and summary.lower()[:40] == headline.lower()[:40]:
        summary = ""
    return headline, summary


def _finnhub_items(symbol: str) -> list[_News]:
    if not settings.finnhub_api_key:
        return []
    now = datetime.now(timezone.utc)
    frm = now - timedelta(hours=settings.news_max_age_hours)
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": symbol,
                "from": frm.date().isoformat(),
                "to": now.date().isoformat(),
                "token": settings.finnhub_api_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json() or []
    except Exception as exc:
        logger.warning(f"Finnhub news failed for {symbol}: {exc}")
        return []
    return [
        _News((it.get("headline") or "").strip(),
              (it.get("summary") or "").strip(),
              int(it.get("datetime") or 0))
        for it in items if it.get("headline")
    ]


def _yahoo_items(symbol: str) -> list[_News]:
    try:
        raw = yf.Ticker(symbol).news or []
    except Exception as exc:
        logger.warning(f"Yahoo news failed for {symbol}: {exc}")
        return []
    out: list[_News] = []
    for it in raw:
        content = it.get("content", it)
        title = (content.get("title") or "").strip()
        if not title:
            continue
        summary = (content.get("summary") or content.get("description") or "").strip()
        ts = 0
        pub = content.get("pubDate") or content.get("displayTime")
        if pub:
            try:
                ts = int(datetime.fromisoformat(str(pub).replace("Z", "+00:00")).timestamp())
            except ValueError:
                pass
        if not ts and it.get("providerPublishTime"):
            ts = int(it["providerPublishTime"])
        out.append(_News(title, summary, ts))
    return out


def get_catalyst(symbol: str, company_name: str = "") -> tuple[str, str]:
    """Best (headline, summary_sentence) for a symbol within the freshness window.

    Prefers company-specific news over generic roundup headlines, and picks the
    most relevant (ticker/name mention), then most recent. Finnhub first, Yahoo
    as fallback. Either element may be '' if nothing suitable is found.
    """
    headline, summary = _select_catalyst(_finnhub_items(symbol), symbol, company_name)
    if headline:
        return headline, summary
    return _select_catalyst(_yahoo_items(symbol), symbol, company_name)
