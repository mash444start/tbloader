import os
import asyncio
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp
import shutil
from dotenv import load_dotenv

# Load .env file
load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")
bot = AsyncTeleBot(API_TOKEN)

# Create download folder if not exists
if not os.path.exists("downloads"):
    os.makedirs("downloads")

user_platform = {}
download_queue = asyncio.Queue()

# Cookies file for Instagram & Twitter
INSTAGRAM_COOKIES = "insta_cookies.txt"  # Make sure this file exists
TWITTER_COOKIES = "twitter_cookies.txt"  # Make sure this file exists

# Check if ffmpeg is installed
FFMPEG_EXISTS = shutil.which("ffmpeg") is not None
if FFMPEG_EXISTS:
    print("✅ ffmpeg detected. High-quality merge enabled.")
else:
    print("⚠️ ffmpeg not detected. Falling back to single-format downloads.")

# ===== START COMMAND =====
@bot.message_handler(commands=['start'])
async def start(message):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("Instagram", callback_data="instagram"),
        InlineKeyboardButton("X/Twitter", callback_data="twitter")
    )
    await bot.send_message(message.chat.id, "🎯 Choose platform:", reply_markup=markup)

# ===== BUTTON HANDLER =====
@bot.callback_query_handler(func=lambda call: True)
async def callback_query(call):
    user_platform[call.from_user.id] = call.data
    await bot.send_message(call.message.chat.id, "📎 Send the video URL:")

# ===== GENERAL URL HANDLER =====
@bot.message_handler(func=lambda message: True)
async def handle_url(message):
    platform = user_platform.get(message.from_user.id)
    if not platform:
        await bot.send_message(message.chat.id, "❌ Please select a platform first using /start.")
        return

    print(f"✅ Your {platform} video task is queued...")
    progress_message = await bot.send_message(
        message.chat.id,
        "🎬 Your video is downloading, please wait... ⏳"
    )

    await download_queue.put((message.chat.id, message.text.strip(), platform, progress_message.message_id))
    user_platform.pop(message.from_user.id, None)

# ===== WORKER FUNCTION =====
# ===== WORKER FUNCTION FAST VERSION =====
async def download_worker(worker_id):
    while True:
        chat_id, url, platform, progress_msg_id = await download_queue.get()
        try:
            progress_data = {'percent': '0.0%'}

            def progress_hook(d):
                if d['status'] == 'downloading':
                    progress_data['percent'] = d.get('_percent_str', '0.0%')

            print(f"✅ Worker {worker_id}: Started fetching {platform} video URL...")

            def fetch_video_url():
                ydl_opts = {
                    'format': 'bestvideo+bestaudio/best' if FFMPEG_EXISTS else 'best',
                    'noplaylist': True,
                    'quiet': True,
                    'progress_hooks': [progress_hook],
                    'skip_download': True  # Skip local download
                }

                # Add cookies if available
                if platform == "instagram" and os.path.exists(INSTAGRAM_COOKIES):
                    ydl_opts['cookiefile'] = INSTAGRAM_COOKIES
                elif platform == "twitter" and os.path.exists(TWITTER_COOKIES):
                    ydl_opts['cookiefile'] = TWITTER_COOKIES

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)  # cookies applied
                    # direct video URL
                    return info['url'], info.get('ext', 'mp4')

            video_url, ext = await asyncio.to_thread(fetch_video_url)

            # Progress loop (optional, mainly for user feedback)
            async def show_progress():
                last_percent = ''
                while last_percent != '100.0%':
                    percent = progress_data['percent']
                    if percent != last_percent:
                        await bot.edit_message_text(
                            f"🎬 Fetching {platform} video... {percent} ⏳",
                            chat_id,
                            progress_msg_id
                        )
                        last_percent = percent
                    await asyncio.sleep(0.5)

            progress_task = asyncio.create_task(show_progress())

            # ✅ Direct send using video URL
            await bot.send_chat_action(chat_id, "upload_video")
            await bot.send_video(chat_id, video_url)

            progress_task.cancel()
            await bot.edit_message_text(f"✅ Video sent successfully! 🎉", chat_id, progress_msg_id)
            print(f"✅ Worker {worker_id}: Video sent successfully!")

        except Exception as e:
            print(f"❌ Worker {worker_id} error:", e)
            await bot.edit_message_text(
                "❌ Cannot fetch/send the video right now. Please try again later! ⏳",
                chat_id,
                progress_msg_id
            )

        finally:
            download_queue.task_done()


# ===== RUN BOT + WORKERS =====
async def main():
    print("✅ Fast Async Telegram Video Bot with Progress + Cookies is running...")
    workers = [asyncio.create_task(download_worker(i)) for i in range(1, 4)]
    await bot.infinity_polling()
    await asyncio.gather(*workers)

if __name__ == "__main__":
    asyncio.run(main())
