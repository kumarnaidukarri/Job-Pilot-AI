"""
Find your Telegram chat id(s) for TELEGRAM_CHAT_ID.

  1. Create a bot: message @BotFather -> /newbot -> put the token in .env as TELEGRAM_BOT_TOKEN
  2. Each person who should get alerts sends any message (e.g. /start) to the bot
  3. python tools/telegram_setup.py        -> prints chat ids; add them comma-separated
  4. python tools/telegram_setup.py --test -> sends a test alert to TELEGRAM_CHAT_ID
"""
import os
import sys

import requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
load_dotenv(os.path.join(ROOT, ".env"))

token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    sys.exit("TELEGRAM_BOT_TOKEN is not set in .env")

if "--test" in sys.argv:
    from agent.telegram_notify import send_text
    ok = send_text("✅ <b>JobPilot AI</b> is connected. Top job matches will arrive here.\nSend /help for tracker commands.")
    print("sent" if ok else "failed — check TELEGRAM_CHAT_ID")
    sys.exit(0)

res = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
chats = {}
for u in res.get("result", []):
    chat = (u.get("message") or {}).get("chat") or {}
    if chat:
        chats[chat["id"]] = chat.get("first_name") or chat.get("title") or ""
if not chats:
    print("No messages found — send /start to the bot from each phone first, then rerun.")
for cid, name in chats.items():
    print(f"chat id {cid}  ({name})")
if chats:
    print("\nTELEGRAM_CHAT_ID=" + ",".join(str(c) for c in chats))
