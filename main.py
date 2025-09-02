import os
import asyncio
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp
import shutil
from dotenv import load_dotenv

from keep_alive import keep_alive
keep_alive()  # Start Flask server

# Load .env
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError("❌ API_TOKEN not found in .env!")

bot = AsyncTeleBot(API_TOKEN)

# Cookies
INSTAGRAM_COOKIES = "insta_cookies.txt"
TWITTER_COOKIES = "twitter_cookies.txt"

# Check ffmpeg
FFMPEG_EXISTS = shutil.which("ffmpeg") is not None

# User queue & state
user_platform = {}
download_queue = asyncio.Queue()

# ===== /start =====
@bot.message_handler(commands=['start'])
async def start(message):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("Instagram", callback_data="instagram"),
        InlineKeyboardButton("X/Twitter", callback_data="twitter")
    )
    await bot.send_message(message.chat.id, "🎯 Choose platform:", reply_markup=markup)

# ===== BUTTON CALLBACK =====
@bot.callback_query_handler(func=lambda call: True)
async def callback_query(call):
    user_platform[call.from_user.id] = call.data
    await bot.send_message(call.message.chat.id, "📎 Send the video URL:")

# ===== HANDLE URL =====
@bot.message_handler(func=lambda m: True)
async def handle_url(message):
    platform = user_platform.get(message.from_user.id)
    if not platform:
        await bot.send_message(message.chat.id, "❌ Please select a platform first using /start.")
        return

    progress_msg = await bot.send_message(message.chat.id, "🎬 Your video is being processed... ⏳")
    await download_queue.put((message.chat.id, message.text.strip(), platform, progress_msg.message_id))
    user_platform.pop(message.from_user.id, None)

# ===== DOWNLOAD WORKER =====
async def download_worker(worker_id):
    while True:
        chat_id, url, platform, progress_msg_id = await download_queue.get()
        try:
            # Local download path
            os.makedirs("downloads", exist_ok=True)
            file_path = f"downloads/video_{chat_id}.%(ext)s"

            progress_data = {'percent': '0.0%'}

            def progress_hook(d):
                if d['status'] == 'downloading':
                    progress_data['percent'] = d.get('_percent_str', '0.0%')

            def fetch_video():
                ydl_opts = {
                    'format': 'bestvideo+bestaudio/best' if FFMPEG_EXISTS else 'best',
                    'noplaylist': True,
                    'quiet': True,
                    'progress_hooks': [progress_hook],
                    'outtmpl': file_path,
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                      '(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36'
                    },
                    'merge_output_format': 'mp4'
                }

                if platform == "instagram" and os.path.exists(INSTAGRAM_COOKIES):
                    ydl_opts['cookiefile'] = INSTAGRAM_COOKIES
                elif platform == "twitter" and os.path.exists(TWITTER_COOKIES):
                    ydl_opts['cookiefile'] = TWITTER_COOKIES

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return ydl.prepare_filename(info)

            # Progress updater
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

            # Download video locally
            video_file = await asyncio.to_thread(fetch_video)

            # Send to Telegram
            await bot.send_chat_action(chat_id, "upload_video")
            await bot.send_video(chat_id, open(video_file, "rb"))

            progress_task.cancel()
            await bot.edit_message_text("✅ Video sent successfully! 🎉", chat_id, progress_msg_id)
            print(f"✅ Worker {worker_id}: Video sent.")

            # Optional: remove local file after send
            os.remove(video_file)

        except Exception as e:
            print(f"❌ Worker {worker_id} error:", e)
            await bot.edit_message_text("❌ Failed to fetch/send the video. Try again later!", chat_id, progress_msg_id)

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
