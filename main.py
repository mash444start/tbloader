#from keep_alive import keep_alive 
#keep_alive() # Flask server for uptime

# ===== TB_LOADER PRO — Fully Fixed Premium Style + 50MB Limit + HTML Mode =====
import os
import asyncio
import shutil
import hashlib
from datetime import datetime, timezone
from dotenv import load_dotenv

from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

from keep_alive import keep_alive 
keep_alive() # Flask server for uptime


# ===== Load environment =====
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError("API_TOKEN not found in .env!")

bot = AsyncTeleBot(API_TOKEN)

# ===== Global setup =====
FFMPEG_EXISTS = shutil.which("ffmpeg") is not None
download_queue = asyncio.Queue(maxsize=50)
insta_usage = {}
lock = asyncio.Lock()
url_storage = {}

def short_hash(url: str) -> str:
    return hashlib.md5(url.encode('utf-8')).hexdigest()[:12]

# ===== Platform detection =====
def detect_platform(url):
    url = url.lower()
    if "instagram.com" in url: return "instagram"
    if any(x in url for x in ["twitter.com", "x.com", "t.co"]): return "twitter"
    if any(x in url for x in ["facebook.com", "fb.watch", "fb.com"]): return "facebook"
    return None

# ===== /start =====
@bot.message_handler(commands=['start'])
async def start(m):
    msg = (
        "🚀 <b>TB_LOADER v(2.0) — Fast (short) Downloader</b>\n\n"
        "💎 Supports: <b>Instagram</b> • <b>Twitter/X</b> • <b>Facebook</b>\n"
        "🎬 Video & 🎵 Audio in seconds\n"
        "⚠️ <i>Files up to 50MB only</i>\n"
        "🔥 <u>No ads • fast!</u>\n\n"
        "📩 <b>Just paste your link below!</b>"
    )
    await bot.send_message(m.chat.id, msg, parse_mode='HTML')

# ===== Handle messages =====
@bot.message_handler(func=lambda m: True)
async def handle_message(message):
    links = [l.strip() for l in message.text.split() if l.strip().startswith("http")]
    if not links:
        await bot.reply_to(message, "❌ <b>No valid link found!</b>\nSend Instagram/Twitter/Facebook link 🔗", parse_mode='HTML')
        return

    for url in links:
        platform = detect_platform(url)
        if not platform:
            await bot.reply_to(message, f"⚠️ <b>Unsupported link:</b>\n{url}", parse_mode='HTML')
            continue

        # Instagram usage limit
        async with lock:
            today = datetime.now(timezone.utc).date()
            user_id = message.from_user.id
            if user_id not in insta_usage or insta_usage[user_id]["day"] != today:
                insta_usage[user_id] = {"count": 0, "day": today}
            if insta_usage[user_id]["count"] >= 10:
                await bot.reply_to(message, "🚫 <b>Instagram limit:</b> 10/day\n<i>Try tomorrow ⏰</i>", parse_mode='HTML')
                continue
            insta_usage[user_id]["count"] += 1

        key = short_hash(url)
        url_storage[key] = url
        callback_data = f"{key}_{platform}_{message.message_id}"

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🎬 Video", callback_data=f"v_{callback_data}"),
            InlineKeyboardButton("🎵 Audio", callback_data=f"a_{callback_data}")
        )

        pmap = {"instagram": "Instagram", "twitter": "Twitter/X", "facebook": "Facebook"}
        await bot.reply_to(
            message,
            f"✅ <b>{pmap[platform]}</b> Detected!\n<i>Choose format below 👇</i>",
            reply_markup=markup,
            parse_mode='HTML'
        )

# ===== Callback handler =====
@bot.callback_query_handler(func=lambda c: c.data.startswith(("v_", "a_")))
async def handle_callback(call):
    await bot.answer_callback_query(call.id)
    try:
        prefix = call.data[0]
        rest = call.data[2:]
        key, platform, msg_id = rest.rsplit("_", 2)

        url = url_storage.get(key)
        if not url:
            await bot.send_message(call.message.chat.id, "❌ <b>Link expired!</b> Send again.", parse_mode='HTML')
            return

        media_type = "video" if prefix == "v" else "audio"

        status_msg = "⏳ <b>Starting download...</b>\n⚡ <i>Ultra fast processing</i>"
        status = await bot.send_message(
            call.message.chat.id,
            status_msg,
            reply_to_message_id=int(msg_id),
            parse_mode='HTML'
        )
        await download_queue.put((
            call.message.chat.id, url, platform,
            status.message_id, call.from_user.id,
            media_type, int(msg_id)
        ))

        if len(url_storage) > 2000:
            url_storage.clear()

    except Exception as e:
        print("Callback error:", e)
        await bot.send_message(call.message.chat.id, "❌ <b>Error!</b> Try again.", parse_mode='HTML')

# ===== Download worker =====
async def download_worker(worker_id):
    while True:
        chat_id, url, platform, status_id, user_id, media_type, reply_id = await download_queue.get()
        tmp_path = f"/tmp/dl_{chat_id}_{status_id}"
        final_path = None

        try:
            ydl_opts = {
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'outtmpl': f"{tmp_path}.%(ext)s",
                'merge_output_format': 'mp4' if media_type == "video" else None,
            }

            if media_type == "audio":
                if FFMPEG_EXISTS:
                    ydl_opts['format'] = 'bestaudio'
                    ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]
                else:
                    ydl_opts['format'] = 'bestaudio/best'
            else:
                ydl_opts['format'] = 'bestvideo+bestaudio/best' if FFMPEG_EXISTS else 'best'

            info = None
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=True)

            if not info:
                cookie_file = f"{platform}_cookies.txt"
                if os.path.exists(cookie_file):
                    ydl_opts['cookiefile'] = cookie_file
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = await asyncio.to_thread(ydl.extract_info, url, download=True)

            if not info:
                raise Exception("Download failed")

            ext = info.get('ext', 'mp4') if media_type == "video" else 'mp3'
            final_path = f"{tmp_path}.{ext}"
            if not os.path.exists(final_path):
                for f in os.listdir("/tmp"):
                    if f.startswith(f"dl_{chat_id}_{status_id}"):
                        final_path = f"/tmp/{f}"
                        break

            if not os.path.exists(final_path):
                raise Exception("File not found")

            size_mb = os.path.getsize(final_path) / (1024 * 1024)

            await bot.edit_message_text(
                f"⚙️ <b>Processing complete!</b>\nSize: <i>{size_mb:.1f} MB</i>",
                chat_id, status_id,
                parse_mode='HTML'
            )

            if size_mb > 50:
                await bot.edit_message_text(
                    "❌ <b>File too large!</b> Failed to send\n<i>(Max 50MB allowed)</i>",
                    chat_id, status_id,
                    parse_mode='HTML'
                )
            else:
                await bot.edit_message_text("📤 <b>Sending directly...</b>", chat_id, status_id, parse_mode='HTML')
                with open(final_path, 'rb') as f:
                    if media_type == "audio":
                        await bot.send_audio(
                            chat_id, f,
                            reply_to_message_id=reply_id,
                            caption="🎵 <b>Your audio is ready!</b> — TB_Loader Pro",
                            parse_mode='HTML'
                        )
                    else:
                        await bot.send_video(
                            chat_id, f,
                            supports_streaming=True,
                            reply_to_message_id=reply_id,
                            caption="🎬 <b>Your video is ready!</b> — TB_Loader Pro",
                            parse_mode='HTML'
                        )
                await bot.edit_message_text("✅ <b>Sent successfully! Enjoy! 🎉</b>", chat_id, status_id, parse_mode='HTML')

        except Exception as e:
            print(f"Worker {worker_id} error: {e}")
            try:
                await bot.edit_message_text("❌ <b>Download failed!</b>\nTry again", chat_id, status_id, parse_mode='HTML')
            except: pass
        finally:
            for f in os.listdir("/tmp"):
                if f.startswith(f"dl_{chat_id}_{status_id}") or f.startswith(f"m_{chat_id}_{status_id}"):
                    try: os.remove(f"/tmp/{f}")
                    except: pass
            download_queue.task_done()

# ===== Main =====
async def main():
    print("🚀 TB_LOADER PRO STARTED — Premium Style, 50MB Limit, Ultra Stable")
    for i in range(12):
        asyncio.create_task(download_worker(i))
    await bot.infinity_polling()

if __name__ == "__main__":
    asyncio.run(main())


