"""
Symbol universe — S&P 500 + S&P MidCap 400 constituents from Wikipedia.

Index membership guarantees the $1B+ market cap threshold, so no
per-symbol metadata calls are needed. Company names come from Wikipedia
alongside the symbol lists — zero metadata API calls.
"""

from __future__ import annotations

import io
import logging
import requests
import pandas as pd

_HEADERS = {"User-Agent": "trend-join-long/1.0"}

logger = logging.getLogger(__name__)

_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_SP400_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"


def _wiki_table(
    url: str,
    sym_hints: list[str],
    name_hints: list[str],
) -> list[tuple[str, str]]:
    """Parse the first matching constituent table from a Wikipedia page."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        for df in tables:
            cols = [str(c) for c in df.columns]
            sym_col  = next((c for c in cols if any(h.lower() in c.lower() for h in sym_hints)), None)
            name_col = next((c for c in cols if any(h.lower() in c.lower() for h in name_hints)), None)
            if not sym_col or not name_col:
                continue
            pairs: list[tuple[str, str]] = []
            for _, row in df.iterrows():
                sym  = str(row[sym_col]).strip().replace(".", "-")
                name = str(row[name_col]).strip()
                if not sym or sym in ("nan", "-") or len(sym) > 8:
                    continue
                pairs.append((sym, name))
            if pairs:
                logger.info(f"Parsed {len(pairs)} symbols from {url}")
                return pairs
    except Exception as exc:
        logger.warning(f"Wikipedia parse failed ({url}): {exc}")
    return []


def load_us_symbols() -> list[tuple[str, str]]:
    """S&P 500 + S&P MidCap 400 from Wikipedia. Returns (symbol, name) pairs."""
    seen: dict[str, str] = {}

    for url in (_SP500_URL, _SP400_URL):
        pairs = _wiki_table(url, sym_hints=["Symbol", "Ticker"], name_hints=["Security", "Company", "Name"])
        for sym, name in pairs:
            seen.setdefault(sym, name)

    if not seen:
        logger.error("Failed to load US index constituents from Wikipedia")
        return []

    logger.info(f"Loaded {len(seen)} US symbols (S&P 500 + S&P 400)")
    return list(seen.items())
