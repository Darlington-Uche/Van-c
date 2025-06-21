import os
import re
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError
from difflib import SequenceMatcher

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # More detailed logging
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
TARGET_BOT = "@vankedisicoin_bot"
NOTIFICATION_GROUP = os.getenv("NOT" , "")

if not API_ID or not API_HASH or not NOTIFICATION_GROUP:
    logger.critical("Missing required environment variables. Please check API_ID, API_HASH, and NOTIFICATION_GROUP")
    exit(1)

# Global state
last_task_count = 0
last_notification_time = None
client = None
check_interval = 120  # 2 minutes in seconds

def similar(a, b):
    """Calculate text similarity ratio between two strings"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

async def click_button_by_relation(event, target_text, threshold=0.6):
    """
    Click a button that is semantically related to the target text
    Returns True if clicked, False otherwise
    """
    if not event.buttons:
        logger.debug("No buttons available to click")
        return False

    logger.debug(f"Looking for button related to: '{target_text}'")
    
    best_match = None
    best_score = 0
    best_position = (0, 0)

    for r, row in enumerate(event.buttons):
        for c, btn in enumerate(row):
            btn_text = btn.text or ""
            score = similar(btn_text, target_text)
            logger.debug(f"Button '{btn_text}' similarity score: {score:.2f}")
            
            if score > best_score:
                best_score = score
                best_match = btn_text
                best_position = (r, c)

    if best_score >= threshold:
        logger.info(f"Clicking button '{best_match}' (score: {best_score:.2f}) at position {best_position}")
        try:
            await event.click(best_position[0], best_position[1])
            return True
        except RPCError as e:
            logger.error(f"Failed to click button: {e}")
            return False
    else:
        logger.debug(f"No button reached similarity threshold (best was '{best_match}' at {best_score:.2f})")
        return False

async def navigate_to_tasks():
    """Navigate through the bot menus to reach the tasks panel"""
    logger.info("Starting navigation to tasks")
    
    try:
        # Step 1: Ensure we're at the start
        async for msg in client.iter_messages(TARGET_BOT, from_user="me", limit=1):
            if msg.text and "/start" in msg.text.lower():
                logger.debug("Found recent /start message")
                break
        else:
            logger.info("Sending /start command")
            await client.send_message(TARGET_BOT, "/start")
            await asyncio.sleep(2)

        # Step 2: Check welcome message and proceed
        async for msg in client.iter_messages(TARGET_BOT, limit=1):
            if "Welcome to the vankedisi Adventure!" in msg.text:
                logger.debug("Found welcome message")
                if await click_button_by_relation(msg, "go to task"):
                    logger.info("Clicked 'Go to Task Bot' button")
                    await asyncio.sleep(2)
                else:
                    logger.warning("Failed to find 'Go to Task Bot' button")
                    return False

        # Step 3: Check if we're in tasks panel
        async for msg in client.iter_messages(TARGET_BOT, limit=1):
            if "Task Panel" in msg.text:
                logger.debug("Found task panel")
                if await click_button_by_relation(msg, "tasks"):
                    logger.info("Clicked 'Tasks' button")
                    await asyncio.sleep(2)
                    return True
                else:
                    logger.warning("Failed to find 'Tasks' button")
                    return False

        # If we're not where we expect, try to return to main menu
        async for msg in client.iter_messages(TARGET_BOT, limit=1):
            if await click_button_by_relation(msg, "main menu"):
                logger.info("Returned to main menu")
                return False

        logger.warning("Couldn't determine current bot state")
        return False

    except Exception as e:
        logger.error(f"Navigation error: {e}")
        return False

async def get_task_count():
    """Get the number of available tasks"""
    logger.info("Checking for available tasks")
    
    try:
        if not await navigate_to_tasks():
            logger.warning("Failed to navigate to tasks")
            return 0

        async for msg in client.iter_messages(TARGET_BOT, limit=1):
            if "Active Tasks" in msg.text:
                task_count = msg.text.count("🔹 [")
                logger.info(f"Found {task_count} available tasks")
                return task_count
            else:
                logger.debug("Not in active tasks view")
                return 0

    except Exception as e:
        logger.error(f"Error getting task count: {e}")
        return 0

async def send_notification(message):
    """Send notification to the group"""
    try:
        logger.info(f"Sending notification: {message}")
        await client.send_message(NOTIFICATION_GROUP, message)
        return True
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
        return False

async def check_and_notify():
    """Main monitoring loop"""
    global last_task_count, last_notification_time
    
    logger.info("Starting monitoring loop")
    
    while True:
        try:
            current_task_count = await get_task_count()
            logger.debug(f"Current tasks: {current_task_count}, Last tasks: {last_task_count}")
            
            if current_task_count > 0:
                if current_task_count != last_task_count:
                    # New tasks available
                    message = f"🚨 {current_task_count} NEW TASKS AVAILABLE on Vankedisi! Rush to complete them! 🚨"
                    if await send_notification(message):
                        last_notification_time = datetime.now(timezone.utc)
                last_task_count = current_task_count
            else:
                if last_task_count > 0:
                    # Tasks were available but now gone
                    message = "⚠️ No more tasks available on Vankedisi. Keep checking!"
                    await send_notification(message)
                last_task_count = 0
            
            logger.debug(f"Waiting {check_interval} seconds for next check")
            await asyncio.sleep(check_interval)
            
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            await asyncio.sleep(check_interval)

async def main():
    global client
    
    # Get session string from user
    print("\nPlease paste your Telegram session string:")
    session_string = input().strip()
    
    if not session_string:
        logger.error("No session string provided")
        return
    
    logger.info("Initializing client with provided session string")
    
    try:
        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        await client.start()
        
        # Verify connection
        me = await client.get_me()
        logger.info(f"Successfully connected as {me.first_name} (@{me.username})")
        
        # Start monitoring
        await check_and_notify()
        
    except Exception as e:
        logger.error(f"Failed to initialize client: {e}")
    finally:
        if client:
            await client.disconnect()
        logger.info("Client disconnected")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
