import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

# Load environment variables
load_dotenv()
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
NOTIFICATION_GROUP = os.getenv("NOT", "")  # Can be @groupusername or ID

async def main():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start()
    
    try:
        # Send test message
        message = "✅ Test message from script! The bot is working!"
        await client.send_message(NOTIFICATION_GROUP, message)
        print("✅ Message sent successfully!")

    except Exception as e:
        print(f"❌ Failed to send message: {e}")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())