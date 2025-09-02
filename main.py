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
INSTAGRAM_COOKIES = "insta_cookies.txt"
TWITTER_COOKIES = "twitter_cookies.txt"
FACEBOOK_COOKIES = "facebook_cookies.txt"

# ===== FFMPEG CHECK =====
FFMPEG_EXISTS = shutil.which("ffmpeg") is not None
if FFMPEG_EXISTS:
    print("✅ ffmpeg detected. High-quality merge enabled.")
else:
    print("⚠️ ffmpeg not detected. Using single format.")

# ===== STATE =====
user_platform = {}
download_queue = asyncio.Queue()
insta_usage = {}  # ✅ Track Instagram usage


# ===== /start =====
@bot.message_handler(commands=['start'])
async def start(message):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("Instagram", callback_data="instagram"),
        InlineKeyboardButton("X/Twitter", callback_data="twitter"),
        InlineKeyboardButton("Facebook", callback_data="facebook")
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

    # ✅ Instagram-specific limit
    if platform == "instagram":
        today = datetime.utcnow().date()
        usage = insta_usage.get(user_id, {"count": 0, "last_time": None, "day": today})

        # Reset if new day
        if usage["day"] != today:
            usage = {"count": 0, "last_time": None, "day": today}

        # Daily limit
        if usage["count"] >= 10:
            await bot.send_message(message.chat.id, "❌ Daily limit reached (10 successful videos). Try again tomorrow.")
            return

        # Cooldown only after success
        if usage["last_time"] and datetime.utcnow() - usage["last_time"] < timedelta(minutes=10):
            wait_time = 10 - int((datetime.utcnow() - usage["last_time"]).total_seconds() // 60)
            await bot.send_message(message.chat.id, f"⏳ Please wait {wait_time} minutes before next Instagram download.")
            return

    progress_msg = await bot.send_message(message.chat.id, "🎬 Your video is being processed... ⏳")
    await download_queue.put((message.chat.id, message.text.strip(), platform, progress_msg.message_id, user_id))
    user_platform.pop(message.from_user.id, None)


# ===== WORKER =====
async def download_worker(worker_id):
    while True:
        chat_id, url, platform, progress_msg_id, user_id = await download_queue.get()
        try:
            progress_data = {'percent': '0.0%'}

            def progress_hook(d):
                if d['status'] == 'downloading':
                    progress_data['percent'] = d.get('_percent_str', '0.0%')

            tmp_file = f"/tmp/video_{chat_id}.%(ext)s"

            def download_video():
                opts = {
                    'format': 'bestvideo+bestaudio/best' if FFMPEG_EXISTS else 'best',
                    'noplaylist': True,
                    'quiet': True,
                    'progress_hooks': [progress_hook],
                    'outtmpl': tmp_file
                }

                if platform == "instagram" and os.path.exists(INSTAGRAM_COOKIES):
                    opts['cookiefile'] = INSTAGRAM_COOKIES
                elif platform == "twitter" and os.path.exists(TWITTER_COOKIES):
                    opts['cookiefile'] = TWITTER_COOKIES
                elif platform == "facebook" and os.path.exists(FACEBOOK_COOKIES):
                    opts['cookiefile'] = FACEBOOK_COOKIES

                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    ext = info.get('ext', 'mp4')
                    file_path = f"/tmp/video_{chat_id}.{ext}"
                    return file_path

            # Run download in background thread
            file_path = await asyncio.to_thread(download_video)

            # Progress update
            async def show_progress():
                last = ''
                while last != '100.0%':
                    pct = progress_data['percent']
                    if pct != last:
                        await bot.edit_message_text(
                            f"🎬 Downloading {platform} video... {pct} ⏳",
                            chat_id, progress_msg_id
                        )
                        last = pct
                    await asyncio.sleep(0.5)

            progress_task = asyncio.create_task(show_progress())

            # Send video
            await bot.send_chat_action(chat_id, "upload_video")
            with open(file_path, "rb") as f:
                await bot.send_video(chat_id, f)

            progress_task.cancel()
            await bot.edit_message_text("✅ Video sent successfully! 🎉", chat_id, progress_msg_id)
            print(f"✅ Worker {worker_id}: Sent {platform} video.")

            # ✅ Update Instagram usage only on success
            if platform == "instagram":
                today = datetime.utcnow().date()
                usage = insta_usage.get(user_id, {"count": 0, "last_time": None, "day": today})
                if usage["day"] != today:
                    usage = {"count": 0, "last_time": None, "day": today}
                usage["count"] += 1
                usage["last_time"] = datetime.utcnow()
                usage["day"] = today
                insta_usage[user_id] = usage

            if os.path.exists(file_path):
                os.remove(file_path)

        except Exception as e:
            print(f"❌ Worker {worker_id} error:", e)
            await bot.edit_message_text(
                "❌ Failed to fetch/send the video. Try again later!",
                chat_id, progress_msg_id
            )
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

