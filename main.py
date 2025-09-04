#from keep_alive import keep_alive 
#keep_alive() # Flask server for uptime

#from keep_alive import keep_alive 
#keep_alive() # Flask server for uptime

import os
import asyncio
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp
import shutil
from datetime import datetime, timedelta
from dotenv import load_dotenv

from keep_alive import keep_alive 
keep_alive() # Flask server for uptime

# ===== ENV LOAD =====
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError("❌ API_TOKEN not found in .env!")

bot = AsyncTeleBot(API_TOKEN)

# ===== COOKIES =====
COOKIE_FILES = {
    "instagram": "insta_cookies.txt",
    "twitter": "twitter_cookies.txt",
    "facebook": "facebook_cookies.txt",
    "youtube": "youtube_cookies.txt"   # ✅ YouTube cookies
}

# ===== FFMPEG CHECK =====
FFMPEG_EXISTS = shutil.which("ffmpeg") is not None
if FFMPEG_EXISTS:
    print("✅ ffmpeg detected. High-quality merge enabled.")
else:
    print("⚠️ ffmpeg not detected. Using single format.")

# ===== STATE =====
user_platform = {}
download_queue = asyncio.Queue()
insta_usage = {}

# ===== /start =====
@bot.message_handler(commands=['start'])
async def start(message):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("Instagram", callback_data="instagram"),
        InlineKeyboardButton("X/Twitter", callback_data="twitter"),
        InlineKeyboardButton("Facebook", callback_data="facebook"),
        InlineKeyboardButton("YouTube", callback_data="youtube")   # ✅ Added YouTube button
    )
    await bot.send_message(message.chat.id, "🎯 Choose platform:", reply_markup=markup)

# ===== BUTTON CALLBACK =====
@bot.callback_query_handler(func=lambda call: True)
async def callback_query(call):
    user_platform[call.from_user.id] = call.data
    await bot.send_message(call.message.chat.id, "📎 Send the video URL:")

# ===== HANDLE URL =====
@bot.message_handler(func=lambda message: True)
async def handle_url(message):
    platform = user_platform.get(message.from_user.id)
    if not platform:
        await bot.send_message(message.chat.id, "❌ Please select a platform first using /start.")
        return

    user_id = message.from_user.id

    # Instagram daily/interval limit
    if platform == "instagram":
        today = datetime.utcnow().date()
        usage = insta_usage.get(user_id, {"count": 0, "last_time": None, "day": today})
        if usage["day"] != today:
            usage = {"count": 0, "last_time": None, "day": today}
        if usage["count"] >= 10:
            await bot.send_message(message.chat.id, "❌ Daily limit reached (10 videos). Try again tomorrow.")
            return
        if usage["last_time"] and datetime.utcnow() - usage["last_time"] < timedelta(minutes=10):
            wait_time = 10 - int((datetime.utcnow() - usage["last_time"]).total_seconds() // 60)
            await bot.send_message(message.chat.id, f"⏳ Wait {wait_time} minutes before next download.")
            return

    progress_msg = await bot.send_message(message.chat.id, "✅ URL received. Please wait, downloading...")
    await download_queue.put((message.chat.id, message.text.strip(), platform, progress_msg.message_id, user_id))
    user_platform.pop(message.from_user.id, None)

# ===== WORKER =====
async def download_worker(worker_id):
    while True:
        chat_id, url, platform, progress_msg_id, user_id = await download_queue.get()
        tmp_file = f"/tmp/video_{chat_id}.%(ext)s"
        try:
            # Download video
            def download_video():
                opts = {
                    'format': 'bestvideo+bestaudio/best' if FFMPEG_EXISTS else 'best',
                    'noplaylist': True,
                    'quiet': True,
                    'outtmpl': tmp_file
                }
                # ✅ Universal cookie check
                cookie_path = COOKIE_FILES.get(platform)
                if cookie_path and os.path.exists(cookie_path):
                    opts['cookiefile'] = cookie_path

                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    ext = info.get('ext', 'mp4')
                    return f"/tmp/video_{chat_id}.{ext}"

            await bot.edit_message_text("✅ Download complete. Checking file size...", chat_id, progress_msg_id)
            file_path = await asyncio.to_thread(download_video)

            # Check size
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > 49:
                await bot.edit_message_text(f"❌ File too large ({size_mb:.2f} MB). Try smaller than 49 MB.", chat_id, progress_msg_id)
                os.remove(file_path)
                continue

            await bot.edit_message_text("🎬 File size OK. Sending video, please wait...", chat_id, progress_msg_id)

            # Send video
            await bot.send_chat_action(chat_id, "upload_video")
            with open(file_path, "rb") as f:
                await bot.send_video(chat_id, f)

            await bot.edit_message_text("✅ Video sent successfully! 🎉", chat_id, progress_msg_id)
            print(f"✅ Worker {worker_id}: Sent {platform} video.")

            # Update Instagram usage
            if platform == "instagram":
                today = datetime.utcnow().date()
                usage = insta_usage.get(user_id, {"count": 0, "last_time": None, "day": today})
                if usage["day"] != today:
                    usage = {"count": 0, "last_time": None, "day": today}
                usage["count"] += 1
                usage["last_time"] = datetime.utcnow()
                usage["day"] = today
                insta_usage[user_id] = usage

            os.remove(file_path)

        except Exception as e:
            print(f"❌ Worker {worker_id} error:", e)
            await bot.edit_message_text("❌ Failed to fetch/send the video. Try again later!", chat_id, progress_msg_id)
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        finally:
            download_queue.task_done()

# ===== MAIN =====
async def main():
    print("✅ Render-ready Async Telegram Bot running...")
    workers = [asyncio.create_task(download_worker(i)) for i in range(1, 4)]
    await bot.infinity_polling()
    await asyncio.gather(*workers)

if __name__ == "__main__":
    asyncio.run(main())
