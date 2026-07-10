"""
Telegram notifier — formats the gap list and sends it via the Bot API.

Format (approved mock):

  🔔 TREND JOIN LONG — Gap Scan
  Thu Jul 3 · 7:25 AM MDT · S&P 500+400

  #1  POET  $20.75  +44.34%
      📰 Announces $500M AI datacenter partnership
      PMH $21.10 · YestHigh $14.85 · Stop $20.89 · T1 $22.31 · T2 $23.52

  — PLAN —
  Window 10:00–3:30 ET · Trigger: > PMH and > prior HOD
  ...
"""

from __future__ import annotations

import html
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from scanner.config import settings
from scanner.engine import ScanResult
from scanner.gap import GapHit

logger = logging.getLogger(__name__)

_MT = ZoneInfo("America/Denver")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

_SEND_RETRIES = 3
_SEND_BACKOFF_SECONDS = 3.0


class TelegramSendError(RuntimeError):
    """Raised when a Telegram message could not be delivered after retries."""


# Trade-plan times shown in Mountain Time (ET−2h; holds year-round since
# both zones shift for DST together). ET original: 10:00–3:30, flat 3:51.
_PLAN = (
    "<b>— PLAN —</b>\n"
    "Window 8:00–1:30 MT · Trigger: &gt; PMH and &gt; YestHigh\n"
    "Stop: 1% below PMH or LOD (lower) = 1R\n"
    "Scale ⅓ at +1R, ⅓ at +2R, trail ⅓ on 21-EMA\n"
    "⏰ Flat by 1:51 PM MT"
)


def _send(text: str) -> None:
    """Send one Telegram message, retrying transient failures.

    Raises TelegramSendError if delivery still fails after all retries —
    callers decide whether to let that propagate (crash = a loud, visible
    failure in the job's exit status) or catch it and keep going (e.g. one
    bad send mid-session shouldn't stop the rest of a monitoring run).
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram credentials not configured — skipping notification")
        raise TelegramSendError("Telegram credentials not configured")

    url = _TELEGRAM_API.format(token=settings.telegram_bot_token)
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    last_exc: Exception | None = None
    for attempt in range(1, _SEND_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Telegram notification sent")
            return
        except Exception as exc:
            last_exc = exc
            logger.warning(f"Telegram send attempt {attempt}/{_SEND_RETRIES} failed: {exc}")
            if attempt < _SEND_RETRIES:
                time.sleep(_SEND_BACKOFF_SECONDS * attempt)

    logger.error(f"Telegram send failed after {_SEND_RETRIES} attempts: {last_exc}")
    raise TelegramSendError(f"Telegram send failed after {_SEND_RETRIES} attempts") from last_exc


def _price(v: float) -> str:
    return f"${v:,.2f}"


def _format_hit(rank: int, h: GapHit) -> str:
    lines = [
        f"<b>#{rank}  {h.symbol}  {_price(h.price)}  +{h.gap_pct:.2f}%</b>",
        f"    📰 {html.escape(h.catalyst)}",
    ]
    if h.catalyst_summary:
        lines.append(f"    <i>{html.escape(h.catalyst_summary)}</i>")
    lines.append(
        f"    PMH {_price(h.pm_high)} · YestHigh {_price(h.prev_high)}"
        f" · Stop {_price(h.stop)} · T1 {_price(h.t1)} · T2 {_price(h.t2)}"
    )
    return "\n".join(lines)


def build_message(
    result: ScanResult,
    emoji: str = "🔔",
    title: str = "TREND JOIN LONG — Gap Scan",
) -> str:
    now = datetime.now(_MT).strftime("%a %b %-d · %-I:%M %p %Z")
    tag = " (BACKTEST — last session's open gaps)" if result.mode == "backtest" else ""

    header = (
        f"{emoji} <b>{title}</b>{tag}\n"
        f"{now} · S&amp;P 500+400 · gap &gt; {settings.gap_min_pct:g}%\n"
    )

    if not result.hits:
        return header + "\nNo qualifying gaps today."

    blocks = [_format_hit(i, h) for i, h in enumerate(result.hits, start=1)]
    message = header + "\n" + "\n\n".join(blocks) + "\n\n" + _PLAN

    if len(message) > 4000:  # Telegram hard limit is 4096
        message = message[:3990] + "\n…(truncated)"
    return message


def send_scan_results(result: ScanResult) -> None:
    """Raises TelegramSendError if delivery fails after retries."""
    _send(build_message(result))


def send_text(text: str) -> None:
    """Send a raw HTML message (used by the intraday breakout monitor).

    Raises TelegramSendError if delivery fails after retries.
    """
    _send(text)
