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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')  # Log to file as well
    ]
)
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
    logger.critical("Missing env variables: API_ID, API_HASH, or GROUP_ID")
    exit(1)

# Globals
last_task_count = 0
last_notification_time = None
client = None
check_interval = 60  # seconds
max_retries = 5
retry_delay = 10 # seconds

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
                msg = f"🚨🚨 {count} TASKS AVAILABLE!!🚨🚨"
                await send_notification(msg)
                last_notification_time = datetime.now(timezone.utc)
                last_task_count = count
            elif count == 0 and last_task_count > 0:
                await send_notification("⚠️ No more tasks available. Go and Sleep")
                last_task_count = 0
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
            # If we get an error, try to reconnect
            await reconnect()
        await asyncio.sleep(check_interval)

async def reconnect():
    """Reconnect to Telegram with retries"""
    global client
    for attempt in range(max_retries):
        try:
            logger.info(f"Attempting to reconnect (attempt {attempt + 1}/{max_retries})")
            if client and client.is_connected():
                await client.disconnect()
            
            if SESSION_STRING:
                client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
            else:
                client = TelegramClient(StringSession(), API_ID, API_HASH)
            
            await client.connect()
            
            if not await client.is_user_authorized():
                logger.error("Reconnect failed - not authorized")
                continue
                
            me = await client.get_me()
            logger.info(f"Reconnected successfully as {me.first_name}")
            return True
            
        except Exception as e:
            logger.error(f"Reconnect attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(retry_delay)
    
    logger.critical("Failed to reconnect after multiple attempts")
    return False

async def start_bot():
    global client
    while True:  # Outer loop to restart the bot if it crashes completely
        try:
            logger.info("Starting bot...")
            if not SESSION_STRING:
                client = TelegramClient(StringSession(), API_ID, API_HASH)
                await client.start()
                set_key('.env', 'SESSION_STRING', client.session.save())
            else:
                client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
                await client.start()
            
            me = await client.get_me()
            logger.info(f"Bot started as {me.first_name} (@{me.username})")
            
            # Start monitoring
            await monitor()
            
        except (RPCError, ConnectionError, OSError) as e:
            logger.error(f"Connection error: {e}. Attempting to reconnect...")
            if not await reconnect():
                logger.error("Reconnection failed. Restarting bot...")
                await asyncio.sleep(retry_delay)
                continue
                
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")
            logger.info("Restarting bot in 30 seconds...")
            await asyncio.sleep(retry_delay)
            continue

def run_bot():
    # Create a new event loop for the thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(start_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error in bot thread: {e}")
    finally:
        loop.close()

if __name__ == '__main__':
    # Start bot in a separate thread with its own event loop
    bot_thread = Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Start Flask app
    app.run(host="0.0.0.0", port=5000)