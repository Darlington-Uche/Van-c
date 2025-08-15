import os
import re
import asyncio
import logging
from datetime import datetime, timezone
from threading import Thread
from dotenv import load_dotenv, set_key
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import RPCError
from difflib import SequenceMatcher
from flask import Flask, jsonify

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Flask App
app = Flask(__name__)

@app.route('/health')
def health():
    return jsonify({
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

# Load env variables
load_dotenv()
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
TARGET_BOT = "@vankedisicoin_bot"
NOTIFICATION_GROUP = os.getenv("GROUP_ID", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")

if not API_ID or not API_HASH or not NOTIFICATION_GROUP:
    logger.critical("Missing env variables: API_ID, API_HASH, or NOT")
    exit(1)

# Globals
last_task_count = 0
last_notification_time = None
client = None
check_interval = 120  # seconds

def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

async def click_button_by_relation(event, target_text, threshold=0.6):
    if not event.buttons:
        return False
    best_score = 0
    best_position = (0, 0)
    for r, row in enumerate(event.buttons):
        for c, btn in enumerate(row):
            text = btn.text or ""
            score = similar(text, target_text)
            if score > best_score:
                best_score = score
                best_position = (r, c)
    if best_score >= threshold:
        try:
            await event.click(best_position[0], best_position[1])
            return True
        except RPCError as e:
            logger.error(f"Click error: {e}")
            return False
    return False

async def navigate_to_tasks():
    logger.info("Navigating to tasks without /start")
    try:
        # Step 1: Always try to return to main menu first
        async for msg in client.iter_messages(TARGET_BOT, limit=3):
            if await click_button_by_relation(msg, "main menu"):
                logger.info("Clicked 'Main Menu' to reset bot state")
                await asyncio.sleep(2)
                break

        # Step 2: Look for welcome message and click "Go to Task"
        async for msg in client.iter_messages(TARGET_BOT, limit=3):
            if "Welcome to the vankedisi Adventure!" in msg.text:
                if await click_button_by_relation(msg, "go to task"):
                    logger.info("Clicked 'Go to Task Bot'")
                    await asyncio.sleep(2)
                break

        # Step 3: Find task panel and click 'Tasks'
        async for msg in client.iter_messages(TARGET_BOT, limit=3):
            if "Task Panel" in msg.text:
                if await click_button_by_relation(msg, "tasks"):
                    logger.info("Entered Task Panel")
                    await asyncio.sleep(2)
                    return True

        logger.warning("Failed to reach Task Panel")
        return False

    except Exception as e:
        logger.error(f"Navigation error: {e}")
        return False

async def get_task_count():
    try:
        if not await navigate_to_tasks():
            return 0
        async for msg in client.iter_messages(TARGET_BOT, limit=1):
            if "Active Tasks" in msg.text:
                count = msg.text.count("🔹")
                logger.info(f"Found {count} tasks")
                return count
    except Exception as e:
        logger.error(f"Task count error: {e}")
    return 0

async def send_notification(msg):
    try:
        await client.send_message(NOTIFICATION_GROUP, msg)
    except Exception as e:
        logger.error(f"Notification failed: {e}")

async def monitor():
    global last_task_count, last_notification_time
    while True:
        try:
            count = await get_task_count()
            if count > 0 and count != last_task_count:
                msg = f"🚨 {count} NEW TASKS AVAILABLE on Vankedisi!"
                await send_notification(msg)
                last_notification_time = datetime.now(timezone.utc)
                last_task_count = count
            elif count == 0 and last_task_count > 0:
                await send_notification("⚠️ No more tasks available.")
                last_task_count = 0
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
        await asyncio.sleep(check_interval)

async def start_bot():
    global client
    if not SESSION_STRING:
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.start()
        set_key('.env', 'SESSION_STRING', client.session.save())
    else:
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
        await client.start()
    me = await client.get_me()
    logger.info(f"Bot started as {me.first_name} (@{me.username})")
    await monitor()

def start_loop():
    asyncio.run(start_bot())

if __name__ == '__main__':
    Thread(target=start_loop).start()
    app.run(host="0.0.0.0", port=5000)