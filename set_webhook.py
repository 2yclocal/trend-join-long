#!/usr/bin/env python3
"""
Run once after deploying to Render to register the webhook URL with Telegram.

Usage:
    python set_webhook.py https://trend-join-long-bot.onrender.com

The script reads TELEGRAM_BOT_TOKEN from the environment (or .env file).
"""

import sys
import os
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not token:
    print("Error: TELEGRAM_BOT_TOKEN not set")
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: python set_webhook.py https://your-app.onrender.com")
    sys.exit(1)

base_url = sys.argv[1].rstrip("/")
webhook_url = f"{base_url}/webhook/{token}"

resp = requests.post(
    f"https://api.telegram.org/bot{token}/setWebhook",
    json={"url": webhook_url, "allowed_updates": ["message", "channel_post"]},
    timeout=10,
)
data = resp.json()

if data.get("ok"):
    print(f"✅ Webhook registered: {webhook_url}")
else:
    print(f"❌ Failed: {data}")
