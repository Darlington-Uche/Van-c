import os
import re
import asyncio
import logging
from datetime import datetime, timezone
from threading import Thread
from dotenv import load_dotenv, set_key
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import RPCError, UserAlreadyParticipantError, FloodWaitError, SessionPasswordNeededError
from telethon.tl.functions.messages import ImportChatInviteRequest
from difflib import SequenceMatcher
from flask import Flask, jsonify

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Flask App
app = Flask(__name__)

@app.route('/health')
def health():
    return jsonify({
        "status": "running",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "last_task_count": last_task_count,
        "last_notification_time": last_notification_time.isoformat() if last_notification_time else None
    })

# Load env variables
load_dotenv()
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
TARGET_BOT = "@vankedisicoin_bot"
NOTIFICATION_GROUP = os.getenv("GROUP_ID", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
BOT_OWNER_ID = os.getenv("BOT_OWNER_ID", "")  # Your Telegram user ID for error reports

if not API_ID or not API_HASH:
    logger.critical("Missing env variables: API_ID or API_HASH")
    exit(1)

# Globals
last_task_count = 0
last_notification_time = None
client = None
check_interval = 20  # seconds
max_retries = 5
retry_delay = 2  # seconds
notification_entity = None

def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

async def send_error_notification(error_msg):
    """Send error notifications to both GROUP_ID and BOT_OWNER_ID if set"""
    error_message = f"❌ BOT ERROR ❌\n\n{error_msg}\n\nTime: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    
    # Send to notification group
    if notification_entity:
        try:
            await client.send_message(notification_entity, error_message)
            logger.info("Error notification sent to group")
        except Exception as e:
            logger.error(f"Failed to send error to group: {e}")
    
    # Send to bot owner personally
    if BOT_OWNER_ID:
        try:
            await client.send_message(int(BOT_OWNER_ID), error_message)
            logger.info("Error notification sent to bot owner")
        except Exception as e:
            logger.error(f"Failed to send error to owner: {e}")

async def resolve_notification_entity():
    """Resolve NOTIFICATION_GROUP to a proper Telethon entity with invite link support"""
    global notification_entity
    
    try:
        # Handle private channel invite links
        if NOTIFICATION_GROUP.startswith("https://t.me/+"):
            invite_hash = NOTIFICATION_GROUP.split("+")[-1]
            logger.info(f"Attempting to join private channel with hash: {invite_hash}")
            
            try:
                result = await client(ImportChatInviteRequest(invite_hash))
                notification_entity = result.chats[0]
                logger.info(f"Successfully joined private channel: {notification_entity.title}")
                return True
            except UserAlreadyParticipantError:
                # Already a participant, resolve entity by username or ID
                channel_username = f"@{invite_hash}"
                notification_entity = await client.get_entity(channel_username)
                logger.info(f"Already participant, resolved: {notification_entity.title}")
                return True
            except Exception as e:
                logger.error(f"Failed to join private channel: {e}")
                await send_error_notification(f"Failed to join private channel: {str(e)}")
                return False
        
        # Handle public links and usernames/IDs
        elif NOTIFICATION_GROUP.startswith("https://t.me/"):
            # Extract username from public link
            username = NOTIFICATION_GROUP.split("/")[-1]
            if username.startswith("@"):
                notification_entity = await client.get_entity(username)
            else:
                notification_entity = await client.get_entity(f"@{username}")
            logger.info(f"Resolved public link to: {notification_entity.title}")
            return True
        
        # Handle direct usernames or IDs
        else:
            notification_entity = await client.get_entity(NOTIFICATION_GROUP)
            entity_name = notification_entity.title if hasattr(notification_entity, 'title') else notification_entity.username
            logger.info(f"Notification entity resolved: {entity_name}")
            return True
            
    except Exception as e:
        error_msg = f"Failed to resolve notification entity '{NOTIFICATION_GROUP}': {str(e)}"
        logger.error(error_msg)
        await send_error_notification(error_msg)
        notification_entity = None
        return False

async def click_button_by_relation(event, target_text, threshold=0.6):
    """Click button with similarity matching"""
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
            logger.debug(f"Clicked button '{target_text}' (score: {best_score:.2f})")
            return True
        except RPCError as e:
            logger.error(f"Click error for '{target_text}': {e}")
            return False
    
    logger.debug(f"No suitable button found for '{target_text}' (best score: {best_score:.2f})")
    return False

async def navigate_to_tasks():
    """Navigate to tasks section in the bot"""
    logger.info("Navigating to tasks without /start")
    
    try:
        # Reset bot state by clicking Main Menu if available
        async for msg in client.iter_messages(TARGET_BOT, limit=3):
            if await click_button_by_relation(msg, "main menu"):
                logger.info("Clicked 'Main Menu' to reset bot state")
                await asyncio.sleep(1)
                break

        # Look for welcome message and navigate to tasks
        async for msg in client.iter_messages(TARGET_BOT, limit=3):    
            if msg.text and "Welcome to the vankedisi Adventure!" in msg.text:    
                if await click_button_by_relation(msg, "go to task"):    
                    logger.info("Clicked 'Go to Task Bot'")    
                    await asyncio.sleep(1)    
                break    

        # Enter task panel
        async for msg in client.iter_messages(TARGET_BOT, limit=3):    
            if msg.text and "Task Panel" in msg.text:    
                if await click_button_by_relation(msg, "tasks"):    
                    logger.info("Entered Task Panel")    
                    await asyncio.sleep(1)    
                    return True    

        logger.warning("Failed to reach Task Panel")
        return False    
        
    except Exception as e:
        error_msg = f"Navigation error: {str(e)}"
        logger.error(error_msg)
        await send_error_notification(error_msg)
        return False

async def get_task_count():
    """Get current task count from the bot"""
    try:
        if not await navigate_to_tasks():
            return 0
            
        async for msg in client.iter_messages(TARGET_BOT, limit=1):
            if msg.text and "Active Tasks" in msg.text:
                count = msg.text.count("🔹")
                logger.info(f"Found {count} tasks")
                return count
                
        return 0
        
    except Exception as e:
        error_msg = f"Task count error: {str(e)}"
        logger.error(error_msg)
        await send_error_notification(error_msg)
        return 0

async def send_notification(msg, is_error=False):
    """Send notification to the resolved entity"""
    global notification_entity
    
    if not notification_entity:
        if not await resolve_notification_entity():
            logger.error("Cannot send notification, entity not resolved.")
            return False
    
    try:
        # Add timestamp and bot info to notifications
        if not is_error:
            full_msg = f"{msg}\n\n⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        else:
            full_msg = msg
            
        await client.send_message(notification_entity, full_msg)
        logger.info("Notification sent successfully")
        return True
        
    except Exception as e:
        error_msg = f"Notification failed: {str(e)}"
        logger.error(error_msg)
        await send_error_notification(error_msg)
        return False

async def send_startup_message():
    """Send startup notification"""
    startup_msg = f"""🤖 Bot Started Successfully!

✅ Connected as: {client._self.first_name if client._self else 'Unknown'}
✅ Monitoring: {TARGET_BOT}
✅ Check Interval: {check_interval}s
✅ Started at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}

Bot is now monitoring for tasks..."""
    
    await send_notification(startup_msg)

async def monitor():
    """Main monitoring loop"""
    global last_task_count, last_notification_time
    
    while True:
        try:
            count = await get_task_count()
            logger.info(f"Current task count: {count}, Previous: {last_task_count}")
            
            if count > 0 and count != last_task_count:
                msg = f"""🚨 TASKS AVAILABLE 🚨

📊 Count: {count} tasks
⏰ Detected: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
🎯 Bot: {TARGET_Bot}

Go complete your tasks! 💪"""
                
                if await send_notification(msg):
                    last_notification_time = datetime.now(timezone.utc)
                    last_task_count = count
                    
            elif count == 0 and last_task_count > 0:
                msg = f"""⚠️ NO TASKS AVAILABLE

All tasks completed or unavailable.
Last check: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}

WAGMi 🫡"""
                
                if await send_notification(msg):
                    last_task_count = 0
                    
        except Exception as e:
            error_msg = f"Monitor loop error: {str(e)}"
            logger.error(error_msg)
            await send_error_notification(error_msg)
            await reconnect()
            
        await asyncio.sleep(check_interval)

async def reconnect():
    """Reconnect with error handling"""
    global client
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Reconnection attempt {attempt + 1}/{max_retries}")
            
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
            
            # Re-resolve notification entity after reconnect
            await resolve_notification_entity()
            return True

        except FloodWaitError as e:
            wait_time = e.seconds
            error_msg = f"Flood wait: {wait_time} seconds. Waiting..."
            logger.error(error_msg)
            await send_error_notification(error_msg)
            await asyncio.sleep(wait_time)
            
        except SessionPasswordNeededError:
            error_msg = "Session password needed. Please check your account."
            logger.error(error_msg)
            await send_error_notification(error_msg)
            break
            
        except Exception as e:
            error_msg = f"Reconnect attempt {attempt + 1} failed: {str(e)}"
            logger.error(error_msg)
            await asyncio.sleep(retry_delay)

    critical_error = "Failed to reconnect after multiple attempts"
    logger.critical(critical_error)
    await send_error_notification(critical_error)
    return False

async def start_bot():
    """Main bot startup function"""
    global client
    
    while True:
        try:
            logger.info("Starting bot...")
            
            if not SESSION_STRING:
                client = TelegramClient(StringSession(), API_ID, API_HASH)
                await client.start()
                session_string = client.session.save()
                set_key('.env', 'SESSION_STRING', session_string)
                logger.info("New session created and saved")
            else:
                client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
                await client.start()

            me = await client.get_me()
            logger.info(f"Bot started as {me.first_name} (@{me.username})")

            # Resolve notification entity
            if NOTIFICATION_GROUP:
                await resolve_notification_entity()
                
                # Send startup message
                await send_startup_message()
            else:
                logger.warning("No GROUP_ID set, notifications disabled")

            # Start monitoring
            await monitor()

        except FloodWaitError as e:
            wait_time = e.seconds
            error_msg = f"Flood wait during startup: {wait_time} seconds"
            logger.error(error_msg)
            await send_error_notification(error_msg)
            await asyncio.sleep(wait_time)
            
        except SessionPasswordNeededError:
            error_msg = "Session password needed during startup. Please check your account."
            logger.error(error_msg)
            await send_error_notification(error_msg)
            await asyncio.sleep(30)
            
        except (RPCError, ConnectionError, OSError) as e:
            error_msg = f"Connection error: {str(e)}"
            logger.error(error_msg)
            await send_error_notification(error_msg)
            
            if not await reconnect():
                logger.error("Reconnection failed. Restarting bot...")
                await asyncio.sleep(retry_delay)
                continue

        except Exception as e:
            error_msg = f"Unexpected error in main loop: {str(e)}"
            logger.error(error_msg)
            await send_error_notification(error_msg)
            logger.info("Restarting bot in 30 seconds...")
            await asyncio.sleep(30)
            continue

def run_bot():
    """Run bot in separate thread"""
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