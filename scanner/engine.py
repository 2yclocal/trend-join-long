"""
Scan engine — three-stage premarket gap scan.

Stage 1: batched daily bars for the whole universe → previous close/high.
Stage 2: batched 1-minute premarket bars → last price, PM high, PM volume;
         filter on gap % and price. Survivors are typically a handful.
Stage 3: survivors only — multi-day premarket volume history for RVOL,
         plus one news headline each (the catalyst line).

Backtest mode (for testing when the market is closed): gaps are computed
from the most recent session's OPEN vs the prior close. RVOL and premarket
levels are approximated from daily data so the pipeline can be exercised
end to end.
"""

from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from scanner.config import settings
from scanner.data_provider import (
    ET,
    avg_prior_premarket_volume,
    get_catalyst,
    get_daily_bars,
    get_intraday_bars,
    premarket_slice,
)
from scanner.gap import GapHit, gap_percent, passes_filters

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    hits: list[GapHit]
    symbols_checked: int
    mode: str  # "live" | "backtest"


def _attach_catalysts(hits: list[GapHit]) -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(get_catalyst, h.symbol): h for h in hits}
        for fut in concurrent.futures.as_completed(futures):
            futures[fut].catalyst = fut.result() or "No fresh headline found"


def _finalize(hits: list[GapHit], symbols_checked: int, mode: str) -> ScanResult:
    hits.sort(key=lambda h: h.gap_pct, reverse=True)
    hits = hits[: settings.max_results]
    _attach_catalysts(hits)
    return ScanResult(hits=hits, symbols_checked=symbols_checked, mode=mode)


def run_live_scan(universe: list[tuple[str, str]]) -> ScanResult:
    names = dict(universe)
    symbols = list(names)
    today = datetime.now(ET).date()

    # Stage 1 — previous session close/high
    daily = get_daily_bars(symbols)
    prev: dict[str, tuple[float, float]] = {}
    for sym, df in daily.items():
        df = df[[d < today for d in df.index.date]] if hasattr(df.index, "date") else df
        if df.empty:
            continue
        last = df.iloc[-1]
        prev[sym] = (float(last["Close"]), float(last["High"]))

    # Stage 2 — today's premarket bars for everyone; gap + price filter
    intraday = get_intraday_bars(list(prev), days=1)
    candidates: list[GapHit] = []
    for sym, bars in intraday.items():
        pm = premarket_slice(bars, today)
        pm = pm.dropna(subset=["Close"])
        if pm.empty:
            continue
        prev_close, prev_high = prev[sym]
        price = float(pm["Close"].iloc[-1])
        gap = gap_percent(price, prev_close)
        if price <= settings.min_price or gap <= settings.gap_min_pct:
            continue
        candidates.append(GapHit(
            symbol=sym, company_name=names.get(sym, sym),
            price=price, gap_pct=gap,
            prev_close=prev_close, prev_high=prev_high,
            pm_high=float(pm["High"].max()),
            pm_volume=float(pm["Volume"].sum()),
            rvol=None,
        ))
    logger.info(f"Gap+price filter: {len(candidates)} candidates")

    # Stage 3 — RVOL on the survivors only
    if candidates:
        history = get_intraday_bars(
            [c.symbol for c in candidates], days=settings.rvol_lookback_days
        )
        for c in candidates:
            bars = history.get(c.symbol)
            if bars is None:
                continue
            avg_pm = avg_prior_premarket_volume(bars, today)
            if avg_pm:
                c.rvol = c.pm_volume / avg_pm

    hits = [c for c in candidates if passes_filters(c.price, c.gap_pct, c.rvol)]
    logger.info(f"After RVOL filter: {len(hits)} hits")

    return _finalize(hits, len(symbols), "live")


def run_backtest_scan(universe: list[tuple[str, str]]) -> ScanResult:
    """Pipeline test using the last completed session's open gap."""
    names = dict(universe)
    symbols = list(names)

    daily = get_daily_bars(symbols)
    hits: list[GapHit] = []
    for sym, df in daily.items():
        if len(df) < 2:
            continue
        prior, last = df.iloc[-2], df.iloc[-1]
        price = float(last["Open"])
        gap = gap_percent(price, float(prior["Close"]))
        if price <= settings.min_price or gap <= settings.gap_min_pct:
            continue
        hits.append(GapHit(
            symbol=sym, company_name=names.get(sym, sym),
            price=price, gap_pct=gap,
            prev_close=float(prior["Close"]), prev_high=float(prior["High"]),
            pm_high=price,                       # proxy: no PM data in backtest
            pm_volume=float(last["Volume"]),
            rvol=None,                           # not computable in backtest
        ))

    return _finalize(hits, len(symbols), "backtest")
