#!/usr/bin/env python3
"""
Manual one-shot scan runner.

Usage:
    python run.py               # live premarket scan (run 4:00–9:30 AM ET)
    python run.py --backtest    # pipeline test using last session's open gaps
    python run.py --dry-run     # print the message instead of sending it
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from scanner.universe import load_us_symbols
from scanner.engine import run_live_scan, run_backtest_scan
from scanner.notifier import build_message, send_scan_results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", action="store_true",
                        help="use last session's open gaps (for testing when market is closed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the Telegram message instead of sending it")
    args = parser.parse_args()

    print("=" * 60)
    print(f"  TREND JOIN LONG GAP SCAN — {'backtest' if args.backtest else 'live'} run")
    print("=" * 60)

    universe = load_us_symbols()
    print(f"\nUniverse: {len(universe)} symbols (S&P 500 + S&P 400)\n")

    result = run_backtest_scan(universe) if args.backtest else run_live_scan(universe)

    print("\n" + "=" * 60)
    print(f"  {len(result.hits)} qualifying gap(s) out of {result.symbols_checked} scanned")
    for i, h in enumerate(result.hits, start=1):
        rvol = f"{h.rvol:.1f}x" if h.rvol is not None else "n/a"
        print(f"  #{i}  {h.symbol:<6} ${h.price:<8.2f} +{h.gap_pct:.2f}%  RVOL {rvol}")
        print(f"      {h.catalyst}")
    print("=" * 60)

    if args.dry_run:
        print("\n--- Telegram message (dry run, not sent) ---\n")
        print(build_message(result))
    else:
        print("\nSending Telegram notification…")
        send_scan_results(result)
    print("Done.")


if __name__ == "__main__":
    main()
