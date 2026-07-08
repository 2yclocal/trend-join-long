#!/usr/bin/env python3
"""
Telegram webhook bot — runs on Render free tier.

Telegram pushes each message instantly to the /webhook/<token> endpoint.
Commands are processed in a background thread so Telegram gets a 200 immediately.

Environment variables (set in Render dashboard):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

import sys
import os
import logging
import threading
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, abort

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("webhook_bot")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not set")
    sys.exit(1)

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
_MT = ZoneInfo("America/Denver")

app = Flask(__name__)


# ── Telegram helpers ────────────────────────────────────────────────────────

def _api(method: str, **kwargs) -> dict:
    resp = requests.post(f"{BASE}/{method}", json=kwargs, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _send(chat_id, text: str):
    _api("sendMessage", chat_id=chat_id, text=text,
         parse_mode="HTML", disable_web_page_preview=True)


# ── Command parsing ─────────────────────────────────────────────────────────

def _base_command(text: str) -> str:
    word = text.split()[0] if text.split() else text
    return word.split("@")[0].lower()


def _parse_symbols(text: str) -> list[str]:
    parts = text.split()
    if parts and parts[0].startswith("/"):
        parts = parts[1:]
    return [s.upper() for s in " ".join(parts).replace(",", " ").split() if s]


# ── Backtest ─────────────────────────────────────────────────────────────────

def _run_backtest(symbol: str) -> str:
    from scanner.backtest import run_backtest

    try:
        result = run_backtest(symbol)
    except Exception as exc:
        return f"⚠️ Backtest failed for {symbol}: {str(exc)[:150]}"

    if result.trades_used == 0:
        return (
            f"<b>TJL Backtest — {result.symbol}</b>\n\n"
            f"{result.total_gap_days} qualifying gap day(s) found, but none broke out "
            f"above the trigger — no trades to report."
        )

    lines = [f"<b>TJL Backtest — {result.symbol}</b>"]
    lines.append(
        f"Trades analyzed: {result.trades_used} of {result.triggered} triggered breakouts "
        f"({result.total_gap_days} qualifying gap days total)"
    )
    lines.append(
        f"\n<b>Total P&amp;L: ${result.total_pnl_dollars:+,.0f} ({result.total_pnl_pct:+.2f}%)</b> "
        f"(${100_000:,} per trade, not compounded)"
    )
    lines.append(f"Win rate: {result.wins}/{result.trades_used} ({result.win_rate_pct:.1f}%)")
    lines.append(
        f"Avg trade: {result.avg_pnl_pct:+.2f}% | Best: {result.best_pct:+.2f}% | Worst: {result.worst_pct:+.2f}%"
    )
    lines.append(f"Range: {result.first_trade_date} → {result.last_trade_date}")
    lines.append(
        "\n<i>Win = day's high reached +1.5% from entry before the stop hit. "
        "Daily-bar approximation, not a true intraday replay (see scanner/backtest.py).</i>"
    )
    return "\n".join(lines)


# ── Help text ────────────────────────────────────────────────────────────────

_HELP = (
    "<b>Trend Join Long Bot</b>\n\n"
    "<b>Backtest a symbol's gap/breakout plan (last 100 triggered trades):</b>\n"
    "<code>/backtest AAPL</code>\n"
)


# ── Message handler ──────────────────────────────────────────────────────────

def _handle(msg: dict):
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    if not text or not text.startswith("/"):
        return

    cmd = _base_command(text)
    sent_at = datetime.fromtimestamp(msg["date"], tz=timezone.utc).astimezone(_MT).strftime("%-I:%M %p %Z")
    picked_up = datetime.now(timezone.utc).astimezone(_MT).strftime("%-I:%M %p %Z")

    if cmd in ("/start", "/help"):
        _send(chat_id, _HELP)
        return

    if cmd == "/backtest":
        symbols = _parse_symbols(text)
        if len(symbols) != 1:
            _send(chat_id, "Include exactly one symbol — e.g.\n/backtest AAPL")
            return
        sym = symbols[0]
        logger.info(f"Backtesting: {sym}")
        _send(chat_id, f"📨 Received at {picked_up} (sent {sent_at})\n⏳ Backtesting {sym}…")
        _send(chat_id, _run_backtest(sym))


# ── Flask routes ─────────────────────────────────────────────────────────────

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(silent=True)
    if not update:
        abort(400)

    def _process():
        try:
            msg = update.get("message") or update.get("channel_post")
            if msg:
                _handle(msg)
        except Exception as exc:
            logger.error(f"Handler error: {exc}")

    threading.Thread(target=_process, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
