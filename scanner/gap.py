"""
Gap detection + trade-plan levels.

A stock qualifies when, at scan time (premarket):
  - gap % vs previous close  >  GAP_MIN_PCT   (default 3%)
  - last premarket price     >  MIN_PRICE     (default $3)

(A premarket-volume/RVOL gate was dropped: free data sources return no
premarket volume, so it could not be computed reliably. Size/liquidity is
still gated by S&P 500/400 index membership.)

Trade-plan levels included with each hit:
  trigger = max(premarket high, yesterday's high)
  stop    = premarket high − 1%   (LOD rule applies intraday, per plan text)
  1R      = trigger − stop;  T1 = trigger + 1R,  T2 = trigger + 2R
"""

from __future__ import annotations

from dataclasses import dataclass

from scanner.config import settings


@dataclass
class GapHit:
    symbol: str
    company_name: str
    price: float          # last premarket price (or session open in backtest)
    gap_pct: float
    prev_close: float
    prev_high: float
    pm_high: float
    pm_volume: float
    catalyst: str = ""            # headline
    catalyst_summary: str = ""    # one-sentence context under the headline

    @property
    def trigger(self) -> float:
        return max(self.pm_high, self.prev_high)

    @property
    def stop(self) -> float:
        return self.pm_high * (1 - settings.stop_pct_below_pmh / 100)

    @property
    def one_r(self) -> float:
        return self.trigger - self.stop

    @property
    def t1(self) -> float:
        return self.trigger + self.one_r

    @property
    def t2(self) -> float:
        return self.trigger + 2 * self.one_r


def gap_percent(price: float, prev_close: float) -> float:
    if not prev_close:
        return 0.0
    return (price - prev_close) / prev_close * 100


def passes_filters(price: float, gap_pct: float) -> bool:
    return price > settings.min_price and gap_pct > settings.gap_min_pct
