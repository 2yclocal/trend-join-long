#!/usr/bin/env python3
"""
Intraday breakout monitor — phase 2.

Started by GitHub Actions around the market open. Flow:
  1. Rebuild the morning gap list (same pipeline as the premarket scan —
     the premarket window is closed by then, so the list is stable).
  2. Send a "monitor live" message with each stock's trigger level.
  3. From 10:00 AM to 3:30 PM ET (8:00–1:30 MT), poll 1-minute bars every
     POLL_SECONDS and alert 🚨 the first time a stock trades above its
     trigger = max(premarket high, yesterday's high). One alert per stock.

Stop shown in the alert follows the plan: 1% below the lower of the
premarket high and the session low-of-day at trigger time.

Usage:
    python monitor.py
"""

import logging
import os
import sys
import time as time_mod
from datetime import datetime, time as dtime

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("monitor")

import html

import pandas as pd

from scanner.config import settings
from scanner.data_provider import ET, get_intraday_bars
from scanner.engine import run_live_scan
from scanner.gap import GapHit
from scanner.notifier import _MT, build_message, send_text
from scanner.universe import load_us_symbols

WINDOW_START = dtime(10, 0)    # ET — 8:00 AM MT (user's trade-plan window)
WINDOW_END   = dtime(15, 30)   # ET — 1:30 PM MT
RTH_OPEN     = dtime(9, 30)    # regular session open, for LOD/last price
POLL_SECONDS = 60


def _price(v: float) -> str:
    return f"${v:,.2f}"


def _now_et() -> datetime:
    return datetime.now(ET)


def _session_bars(df: pd.DataFrame, today) -> pd.DataFrame:
    """Regular-session rows (>= 9:30 ET) for today."""
    idx = df.index
    mask = (pd.Index(idx.date) == today) & (pd.Index(idx.time) >= RTH_OPEN)
    return df[mask].dropna(subset=["Close"])


def _breakout_alert(h: GapHit, last: float, lod: float) -> str:
    trig = h.trigger
    stop = min(h.pm_high, lod) * (1 - settings.stop_pct_below_pmh / 100)
    one_r = trig - stop
    now_mt = datetime.now(_MT).strftime("%-I:%M %p %Z")
    return (
        f"🚨 <b>BREAKOUT — {h.symbol}</b> · {now_mt}\n"
        f"{_price(last)} crossed trigger {_price(trig)}\n"
        f"Entry {_price(trig)} · Stop {_price(stop)} · 1R {_price(one_r)}\n"
        f"T1 {_price(trig + one_r)} · T2 {_price(trig + 2 * one_r)}\n"
        f"Gap +{h.gap_pct:.1f}% · PMH {_price(h.pm_high)} · YestHigh {_price(h.prev_high)}\n"
        f"📰 {html.escape(h.catalyst)}"
    )


def main():
    logger.info("Rebuilding the morning gap list…")
    result = run_live_scan(load_us_symbols())
    watch = {h.symbol: h for h in result.hits}

    if not watch:
        logger.info("No gappers to monitor — exiting quietly.")
        return

    # Same rich format as the gap-scan alert (ranked list + levels + plan),
    # with a header that marks it as the monitor going live for the session.
    send_text(build_message(result, emoji="👁", title="TREND JOIN LONG — Monitor Live"))
    logger.info(f"Watching {len(watch)}: {list(watch)}")

    while _now_et().time() < WINDOW_START:
        time_mod.sleep(30)

    triggered: set[str] = set()
    while _now_et().time() < WINDOW_END and len(triggered) < len(watch):
        today = _now_et().date()
        pending = [s for s in watch if s not in triggered]
        try:
            bars = get_intraday_bars(pending, days=1)
        except Exception as exc:
            logger.warning(f"Poll failed: {exc}")
            bars = {}

        for sym in pending:
            df = bars.get(sym)
            if df is None:
                continue
            rth = _session_bars(df, today)
            if rth.empty:
                continue
            last = float(rth["Close"].iloc[-1])
            lod = float(rth["Low"].min())
            h = watch[sym]
            if last > h.trigger:
                triggered.add(sym)
                logger.info(f"BREAKOUT {sym}: {last} > {h.trigger}")
                send_text(_breakout_alert(h, last, lod))

        time_mod.sleep(POLL_SECONDS)

    logger.info(
        f"Session done — {len(triggered)}/{len(watch)} triggered: {sorted(triggered)}"
    )


if __name__ == "__main__":
    main()
