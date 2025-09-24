import os
import re
import asyncio
import logging
from datetime import datetime, timezone
from threading import Thread
from dotenv import load_dotenv, set_key
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import RPCError, ChannelPrivateError, ChatWriteForbiddenError
from difflib import SequenceMatcher
from flask import Flask, jsonify

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
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
NOTIFICATION_GROUP = os.getenv("GROUP_ID", "3071139125")  # Your channel ID
SESSION_STRING = os.getenv("SESSION_STRING", "")

# Convert to integer if it's a numeric ID
try:
    if NOTIFICATION_GROUP and NOTIFICATION_GROUP.strip().lstrip('-').isdigit():
        NOTIFICATION_GROUP = int(NOTIFICATION_GROUP)
except ValueError:
    pass  # Keep as string if it's a username

if not API_ID or not API_HASH or not NOTIFICATION_GROUP:
    logger.critical("Missing env variables: API_ID, API_HASH, or GROUP_ID")
    exit(1)

# Globals
last_task_count = 0
last_notification_time = None
client = None
check_interval = 60
max_retries = 5
retry_delay = 10

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
        # Try to send to the private channel
        await client.send_message(NOTIFICATION_GROUP, msg)
        logger.info(f"Notification sent successfully to {NOTIFICATION_GROUP}")
        return True
        
    except ChannelPrivateError:
        logger.error("Channel is private and bot doesn't have access. Please add the bot to the channel as admin.")
        return False
        
    except ChatWriteForbiddenError:
        logger.error("Bot doesn't have permission to send messages in this channel. Please check admin permissions.")
        return False
        
    except ValueError as e:
        logger.error(f"Invalid chat ID: {e}")
        return False
        
    except Exception as e:
        logger.error(f"Notification failed: {e}")
        return False

async def check_channel_access():
    """Check if we can access the notification channel"""
    try:
        # Try to get channel info
        channel = await client.get_entity(NOTIFICATION_GROUP)
        logger.info(f"Channel info: {channel}")
        
        # Try to send a test message
        test_msg = await client.send_message(NOTIFICATION_GROUP, "🤖 Bot started successfully! Monitoring tasks...")
        await asyncio.sleep(2)
        await test_msg.delete()  # Clean up test message
        
        logger.info("Channel access verified")
        return True
        
    except ChannelPrivateError:
        logger.error("❌ Cannot access private channel. Please:")
        logger.error("1. Add your bot as ADMIN to the channel")
        logger.error("2. Grant 'Send Messages' permission")
        logger.error("3. Make sure the channel ID is correct")
        return False
        
    except Exception as e:
        logger.error(f"Channel access check failed: {e}")
        return False

async def monitor():
    global last_task_count, last_notification_time
    while True:
        try:
            count = await get_task_count()
            logger.info(f"Current task count: {count}, Previous: {last_task_count}")
            
            if count > 0 and count != last_task_count:
                msg = f"🚨🚨 {count} TASKS AVAILABLE!!🚨🚨"
                success = await send_notification(msg)
                if success:
                    last_notification_time = datetime.now(timezone.utc)
                    last_task_count = count
                else:
                    logger.error("Failed to send notification - check channel access")
                    
            elif count == 0 and last_task_count > 0:
                msg = "⚠️ God don Catch una, U Think say you wise ^dey play^ supe otilorrŕr"
                success = await send_notification(msg)
                if success:
                    last_task_count = 0
                    
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
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
            
            # Verify channel access after reconnection
            await check_channel_access()
            return True

        except Exception as e:
            logger.error(f"Reconnect attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(retry_delay)

    logger.critical("Failed to reconnect after multiple attempts")
    return False

async def start_bot():
    global client
    while True:
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

            # Check channel access before starting monitoring
            if await check_channel_access():
                logger.info("Channel access verified, starting monitoring...")
                await monitor()
            else:
                logger.error("Channel access failed. Waiting 60 seconds before retry...")
                await asyncio.sleep(60)

        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")
            logger.info("Restarting bot in 30 seconds...")
            await asyncio.sleep(30)
            continue

def run_bot():
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
    bot_thread = Thread(target=run_bot, daemon=True)
    bot_thread.start()

    app.run(host="0.0.0.0", port=5000)