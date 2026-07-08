"""
Gap-and-breakout backtester — replays the trade plan (trigger, then fixed
%-from-entry stop/T1/T2) over historical daily bars for one symbol.

Data limitation: yfinance only retains 1-minute premarket bars for a few
recent weeks, so a true bar-by-bar intraday replay isn't possible across
100 historical trades. This uses daily OHLC as a proxy, same spirit as
run_backtest_scan() in engine.py:

  - Gap day: open[i] vs close[i-1] > gap_min_pct, open[i] > min_price
    (mirrors gap.passes_filters).
  - pm_high proxy = open[i] (no premarket data in history).
  - trigger/entry = max(pm_high proxy, prev_high) — mirrors GapHit.trigger.
    If the day's high never reaches the trigger, the breakout never
    fired intraday — no trade, not counted.
  - stop = entry × (1 − stop_pct%), T1 = entry × (1 + t1_pct%),
    T2 = entry × (1 + t2_pct%) — all fixed percentages off entry
    (backtest-specific; diverges from the live alert's GapHit.stop, which
    is pegged to premarket high rather than entry).
  - Exit: daily bars can't reveal whether the stop or a target was
    touched first when both fall inside the day's range, so the stop is
    checked first (a trade stopped out is always a loss, even if a target
    would have been reached later that day), then T2, then T1, else exit
    at the close (flat by day's end per the trade plan) — a win or loss
    depending on where it closed relative to entry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from scanner.data_provider import get_daily_bars_full
from scanner.gap import gap_percent, passes_filters

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    date: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    outcome: str  # "stop" | "T1" | "T2" | "close"


@dataclass
class BacktestResult:
    symbol: str
    total_gap_days: int
    triggered: int
    trades_used: int
    wins: int
    win_rate_pct: float
    total_pnl_pct: float
    total_pnl_dollars: float
    avg_pnl_pct: float
    best_pct: float
    worst_pct: float
    first_trade_date: str | None
    last_trade_date: str | None


TRADE_SIZE = 100_000  # fixed notional per trade — not compounded
STOP_PCT = 1.0  # stop = entry - 1%
T1_PCT = 1.0    # T1 = entry + 1%
T2_PCT = 2.0    # T2 = entry + 2%


def run_backtest(
    symbol: str,
    max_trades: int = 100,
    stop_pct: float = STOP_PCT,
    t1_pct: float = T1_PCT,
    t2_pct: float = T2_PCT,
) -> BacktestResult:
    df = get_daily_bars_full(symbol)
    if len(df) < 3:
        raise ValueError(f"Insufficient data: {len(df)} bars")

    open_ = df["open"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    dates = df.index

    total_gap_days = 0
    trades: list[Trade] = []

    for i in range(1, len(df)):
        gap = gap_percent(open_[i], close[i - 1])
        if not passes_filters(open_[i], gap):
            continue
        total_gap_days += 1

        pm_high_proxy = open_[i]
        prev_high = high[i - 1]
        trigger = max(pm_high_proxy, prev_high)
        if high[i] < trigger:
            continue  # gap qualified but never broke out — no trade

        stop = trigger * (1 - stop_pct / 100)
        t1 = trigger * (1 + t1_pct / 100)
        t2 = trigger * (1 + t2_pct / 100)

        if low[i] <= stop:
            exit_price, outcome = stop, "stop"
        elif high[i] >= t2:
            exit_price, outcome = t2, "T2"
        elif high[i] >= t1:
            exit_price, outcome = t1, "T1"
        else:
            exit_price, outcome = close[i], "close"

        pnl_pct = (exit_price - trigger) / trigger * 100
        trades.append(Trade(dates[i].strftime("%Y-%m-%d"), trigger, exit_price, pnl_pct, outcome))

    triggered = len(trades)

    if not trades:
        return BacktestResult(
            symbol=symbol, total_gap_days=total_gap_days, triggered=0,
            trades_used=0, wins=0, win_rate_pct=0.0,
            total_pnl_pct=0.0, total_pnl_dollars=0.0, avg_pnl_pct=0.0,
            best_pct=0.0, worst_pct=0.0,
            first_trade_date=None, last_trade_date=None,
        )

    used = trades[-max_trades:]
    pnl_pcts = [t.pnl_pct for t in used]

    dollar_pnls = [p / 100 * TRADE_SIZE for p in pnl_pcts]
    total_pnl_dollars = sum(dollar_pnls)
    total_pnl_pct = total_pnl_dollars / TRADE_SIZE * 100

    wins = sum(1 for p in pnl_pcts if p > 0)

    return BacktestResult(
        symbol=symbol,
        total_gap_days=total_gap_days,
        triggered=triggered,
        trades_used=len(used),
        wins=wins,
        win_rate_pct=round(100 * wins / len(used), 1),
        total_pnl_pct=round(total_pnl_pct, 2),
        total_pnl_dollars=round(total_pnl_dollars, 2),
        avg_pnl_pct=round(sum(pnl_pcts) / len(used), 2),
        best_pct=round(max(pnl_pcts), 2),
        worst_pct=round(min(pnl_pcts), 2),
        first_trade_date=used[0].date,
        last_trade_date=used[-1].date,
    )
