#!/usr/bin/env python3
# TB_LOADER PRO+ (v3.2) — Inline Enhanced (Fixed thumbnail)
# Features: inline single-link edit, batch links, tmp cleaner, usage persist, thumbnail fix, 
# HTML fallback for >50MB, ffmpeg detection, cookie fallback, cooldown, insta limit, graceful shutdown.

import os
import time
import asyncio
import shutil
import hashlib
import json
import signal
import atexit
from datetime import datetime, timezone

from dotenv import load_dotenv
import yt_dlp
import aiohttp
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


from keep_alive import keep_alive 
keep_alive() # Flask server for uptime


# ===== Config =====
USAGE_FILE = "usage.json"
INSTA_FILE = "insta_usage.json"
URL_TTL_SECONDS = 60 * 60  # 1 hour
MAX_URL_STORAGE = 2000
MAX_WORKERS = 12
TMP_CLEAN_INTERVAL = 3600  # seconds
COOLDOWN_SECONDS = 3
MAX_INSTA_PER_DAY = 10
MAX_SEND_MB = 50
TMP_DIR = "/tmp"

# ===== Load .env =====
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise RuntimeError("API_TOKEN not found in .env!")

bot = AsyncTeleBot(API_TOKEN)

# ===== Globals =====
FFMPEG_EXISTS = shutil.which("ffmpeg") is not None
download_queue = asyncio.Queue(maxsize=500)
insta_usage = {}  # persisted per user for day tracking (in-memory)
user_data = {}    # persisted usage stats
lock = asyncio.Lock()
url_storage = {}  # key -> {url, created_at, platform, msg_id, inline(bool), orig_msg_id}
cooldown = {}     # user_id -> last_request_ts

# ===== Persistent usage load/save =====
USAGE_FILE = "usage.json"
INSTA_FILE = "insta_usage.json"

# ===== Persistent usage load/save =====
def load_usage():
    global user_data, insta_usage
    try:
        if os.path.exists(USAGE_FILE):
            with open(USAGE_FILE, "r") as f:
                user_data = json.load(f)
        else:
            user_data = {}
    except Exception as e:
        print("load_usage error:", e)
        user_data = {}

    try:
        if os.path.exists(INSTA_FILE):
            with open(INSTA_FILE, "r") as f:
                insta_usage = json.load(f)
        else:
            insta_usage = {}
    except Exception as e:
        print("load_insta_usage error:", e)
        insta_usage = {}

def save_usage():
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump(user_data, f)
    except Exception as e:
        print("save_usage error:", e)

    try:
        with open(INSTA_FILE, "w") as f:
            json.dump(insta_usage, f)
    except Exception as e:
        print("save_insta_usage error:", e)

# periodic auto-save every 60 sec
async def auto_save_loop():
    while True:
        await asyncio.sleep(60)
        save_usage()

atexit.register(save_usage)

def _handle_exit(sig, frame):
    print(f"Received exit {sig}, saving data...")
    save_usage()
    try:
        loop = asyncio.get_event_loop()
        loop.stop()
    except Exception:
        pass

signal.signal(signal.SIGINT, _handle_exit)
signal.signal(signal.SIGTERM, _handle_exit)
load_usage()

# ===== Helpers =====
def short_hash(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]

def detect_platform(url: str):
    u = url.lower()
    if "instagram.com" in u: return "instagram"
    if any(x in u for x in ["twitter.com", "x.com", "t.co"]): return "twitter"
    if any(x in u for x in ["facebook.com", "fb.watch", "fb.com"]): return "facebook"
    if any(x in u for x in ["tiktok.com", "vm.tiktok.com"]): return "tiktok"
    return None

async def shorten_url(url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://is.gd/create.php?format=simple&url={url}") as r:
                if r.status == 200:
                    txt = await r.text()
                    return txt.strip()
    except Exception:
        pass
    return url

def cleanup_url_storage():
    now = time.time()
    to_del = [k for k,v in url_storage.items() if now - v.get("created_at",0) > URL_TTL_SECONDS]
    for k in to_del:
        url_storage.pop(k, None)

# ===== Inline Keyboard Command Helpers (EDIT IN PLACE) =====
async def send_start_keyboard(chat_id, msg_id=None):
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("📄 Profile", callback_data="profile"),
        InlineKeyboardButton("📊 Stats", callback_data="stats"),
        InlineKeyboardButton("ℹ️ Help", callback_data="help"),
        InlineKeyboardButton("📝 About", callback_data="about")
    )
    msg = (
        "🚀 <b>TB_LOADER v(3.2) PRO+</b> — Fast Downloader\n\n"
        "💎 Supports: <b>Instagram</b> • <b>Twitter/X</b> • <b>Facebook</b> • <b>TikTok</b>\n"
        "🎬 Video & 🎵 Audio in seconds\n"
        f"⚠️ <i>Files up to {MAX_SEND_MB}MB</i>\n\n"
        "📩 <b>Paste one or more links below (space/newline separated)</b>\n\n"
        "• Single link → inline buttons (clean chat)\n• Multiple links → batch replies"
    )
    if msg_id:
        await bot.edit_message_text(msg, chat_id, msg_id, parse_mode="HTML", reply_markup=markup)
    else:
        sent = await bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return sent.message_id

async def send_profile_keyboard(chat_id, user_id, msg_id=None):
    uid = str(user_id)
    d = user_data.get(uid, {})
    downloads = d.get("downloads", 0)
    total_mb = d.get("total_mb", 0.0)
    last = d.get("last_download", "N/A")
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🏠 Start", callback_data="start"),
        InlineKeyboardButton("ℹ️ Help", callback_data="help")
    )
    msg = f"👤 <b>Your Profile</b>\nDownloads: {downloads}\nTotal Data: {total_mb:.1f} MB\nLast: {last}"
    if msg_id:
        await bot.edit_message_text(msg, chat_id, msg_id, parse_mode="HTML", reply_markup=markup)
    else:
        sent = await bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return sent.message_id

async def send_help_keyboard(chat_id, msg_id=None):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🏠 Start", callback_data="start"),
        InlineKeyboardButton("📄 Profile", callback_data="profile")
    )
    msg = (
        "🛠 <b>How to use TB_LOADER</b>\n\n"
        "• Paste link(s) to Instagram/Twitter/X/Facebook/TikTok\n"
        "• For a single link the bot shows inline buttons (Video / Audio) — tap to start\n"
        "• Use /profile to see your usage, /stats for bot stats\n\n"
        f"Limits: Instagram {MAX_INSTA_PER_DAY}/day per user. Global cooldown: {COOLDOWN_SECONDS}s per user."
    )
    if msg_id:
        await bot.edit_message_text(msg, chat_id, msg_id, parse_mode="HTML", reply_markup=markup)
    else:
        sent = await bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return sent.message_id

async def send_stats_keyboard(chat_id, msg_id=None):
    qsize = download_queue.qsize()
    ff = "✅" if FFMPEG_EXISTS else "❌"
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🏠 Start", callback_data="start"),
        InlineKeyboardButton("ℹ️ Help", callback_data="help")
    )
    msg = f"📊 <b>System Stats</b>\nQueue: {qsize}\nWorkers: {MAX_WORKERS}\nFFmpeg: {ff}"
    if msg_id:
        await bot.edit_message_text(msg, chat_id, msg_id, parse_mode="HTML", reply_markup=markup)
    else:
        sent = await bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return sent.message_id

async def send_about_keyboard(chat_id, msg_id=None):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🏠 Start", callback_data="start"),
        InlineKeyboardButton("ℹ️ Help", callback_data="help")
    )
    msg = "TB_LOADER PRO+ — by your dev. Version: v3.2 (Inline Enhanced)"
    if msg_id:
        await bot.edit_message_text(msg, chat_id, msg_id, parse_mode="HTML", reply_markup=markup)
    else:
        sent = await bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return sent.message_id

# ===== Bot commands (updated to use edit-in-place) =====
@bot.message_handler(commands=["start"])
async def start(m):
    await send_start_keyboard(m.chat.id)

@bot.message_handler(commands=["profile"])
async def profile(m):
    await send_profile_keyboard(m.chat.id, m.from_user.id)

@bot.message_handler(commands=["help"])
async def help_cmd(m):
    await send_help_keyboard(m.chat.id)

@bot.message_handler(commands=["stats"])
async def stats(m):
    await send_stats_keyboard(m.chat.id)

@bot.message_handler(commands=["about"])
async def about(m):
    await send_about_keyboard(m.chat.id)

# ===== Inline callback handler for menu navigation (edit in place) =====
@bot.callback_query_handler(func=lambda c: c.data in ["start","profile","help","stats","about"])
async def inline_commands(call):
    await bot.answer_callback_query(call.id)
    cmd = call.data
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    msg_id = call.message.message_id  # Edit this message instead of sending new

    if cmd == "start": 
        await send_start_keyboard(chat_id, msg_id)
    elif cmd == "profile": 
        await send_profile_keyboard(chat_id, user_id, msg_id)
    elif cmd == "help": 
        await send_help_keyboard(chat_id, msg_id)
    elif cmd == "stats": 
        await send_stats_keyboard(chat_id, msg_id)
    elif cmd == "about": 
        await send_about_keyboard(chat_id, msg_id)


# ===== Message handler =====
@bot.message_handler(func=lambda m: True)
async def handle_message(message):
    text = (message.text or "").strip()
    if not text:
        await bot.reply_to(message, "❌ <b>No valid link found!</b>", parse_mode="HTML")
        return

    uid = message.from_user.id
    now = time.time()
    last_ts = cooldown.get(uid, 0)
    if now - last_ts < COOLDOWN_SECONDS:
        await bot.reply_to(message, f"⏳ Please wait {COOLDOWN_SECONDS} seconds between requests.")
        return
    cooldown[uid] = now

    links = [l.strip() for l in text.split() if l.strip().startswith(("http://","https://"))]
    if not links:
        await bot.reply_to(message, "❌ <b>No valid link found!</b> Send Instagram/Twitter/Facebook/TikTok link 🔗", parse_mode="HTML")
        return

    single = len(links) == 1

    for url in links:
        platform = detect_platform(url)
        if not platform:
            await bot.reply_to(message, f"⚠️ <b>Unsupported link:</b>\n{url}", parse_mode="HTML")
            continue

        async with lock:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            user_key = str(uid)
            rec = insta_usage.get(user_key, {})
            if rec.get("day") != today:
                # new day, reset
                rec = {"count": 0, "day": today}
            if platform == "instagram":
                if rec["count"] >= MAX_INSTA_PER_DAY:
                    await bot.reply_to(message, "🚫 <b>Instagram limit:</b> 10/day\n<i>Try tomorrow ⏰</i>", parse_mode="HTML")
                    continue
                rec["count"] += 1
            insta_usage[user_key] = rec
            save_usage() 


        key = short_hash(url + str(time.time()))
        callback_data = f"{key}_{platform}_{message.message_id}"
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🎬 Video", callback_data=f"v_{callback_data}"),
            InlineKeyboardButton("🎵 Audio", callback_data=f"a_{callback_data}")
        )

        pmap = {"instagram":"Instagram","twitter":"Twitter/X","facebook":"Facebook","tiktok":"TikTok"}

        if single:
            sent = await bot.send_message(message.chat.id,
                f"✅ <b>{pmap[platform]}</b> Detected!\n<i>Choose format below 👇</i>",
                reply_markup=markup,
                parse_mode="HTML"
            )
            url_storage[key] = {"url": url, "created_at": time.time(), "platform": platform, "msg_id": sent.message_id, "inline": True, "orig_msg_id": message.message_id}
        else:
            sent = await bot.reply_to(message,
                f"✅ <b>{pmap[platform]}</b> Detected!\n<i>Choose format below 👇</i>",
                reply_markup=markup,
                parse_mode="HTML"
            )
            url_storage[key] = {"url": url, "created_at": time.time(), "platform": platform, "msg_id": sent.message_id, "inline": False, "orig_msg_id": message.message_id}

    if len(url_storage) > MAX_URL_STORAGE:
        cleanup_url_storage()

# ===== Callback handler =====
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith(("v_","a_")))
async def handle_callback(call):
    await bot.answer_callback_query(call.id)
    try:
        prefix = call.data[0]
        rest = call.data[2:]
        key, platform, orig_msgid = rest.rsplit("_", 2)
        rec = url_storage.get(key)
        if not rec:
            try:
                await bot.send_message(call.message.chat.id, "❌ <b>Link expired!</b> Send again.", parse_mode="HTML")
            except:
                pass
            return

        url = rec["url"]
        media_type = "video" if prefix == "v" else "audio"
        msg_id_to_edit = rec.get("msg_id")
        chat_id = call.message.chat.id

        try:
            await bot.edit_message_text("⏳ <b>Starting download...</b>\n⚡ <i>Processing</i>", chat_id, msg_id_to_edit, parse_mode="HTML")
        except Exception:
            status_msg = await bot.send_message(chat_id, "⏳ <b>Starting download...</b>\n⚡ <i>Processing</i>", parse_mode="HTML")
            msg_id_to_edit = status_msg.message_id

        await download_queue.put((chat_id, url, platform, msg_id_to_edit, call.from_user.id, media_type, rec.get("orig_msg_id", None), key))
    except Exception as e:
        print("Callback error:", e)
        try:
            await bot.send_message(call.message.chat.id, "❌ <b>Error!</b> Try again.", parse_mode="HTML")
        except:
            pass

# ===== Download Worker =====
async def download_worker(worker_id:int):
    while True:
        chat_id, url, platform, status_id, user_id, media_type, reply_to_user_msgid, url_key = await download_queue.get()
        timestamp = int(time.time())
        tmp_base = f"{TMP_DIR}/dl_{chat_id}_{status_id}_{timestamp}"
        final_path = None
        try:
            ydl_opts = {
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "outtmpl": f"{tmp_base}.%(ext)s",
            }

            if media_type == "audio":
                if FFMPEG_EXISTS:
                    ydl_opts["format"] = "bestaudio"
                    ydl_opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}]
                else:
                    ydl_opts["format"] = "bestaudio/best"
            else:
                if FFMPEG_EXISTS:
                    ydl_opts["format"] = "bestvideo+bestaudio/best"
                    ydl_opts["merge_output_format"] = "mp4"
                else:
                    ydl_opts["format"] = "best"

            info = None
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=True)

            if not info:
                cookie_file = f"{platform}_cookies.txt"
                if os.path.exists(cookie_file):
                    ydl_opts["cookiefile"] = cookie_file
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = await asyncio.to_thread(ydl.extract_info, url, download=True)

            if not info:
                raise Exception("Download failed (no info)")

            ext = info.get("ext", "mp4") if media_type == "video" else "mp3"
            candidate = f"{tmp_base}.{ext}"
            if os.path.exists(candidate):
                final_path = candidate
            else:
                for f in os.listdir(TMP_DIR):
                    if f.startswith(os.path.basename(tmp_base)):
                        final_path = os.path.join(TMP_DIR, f)
                        break

            if not final_path or not os.path.exists(final_path):
                raise Exception("File not found after download")

            size_mb = os.path.getsize(final_path) / (1024*1024)

            # ===== Thumbnail fix: don't send thumbnail for video =====
            thumb = None
            if media_type == "audio" and isinstance(info, dict):
                thumb = info.get("thumbnail")
            if thumb:
                try:
                    reply_to = reply_to_user_msgid or status_id
                    await bot.send_photo(chat_id, thumb, reply_to_message_id=reply_to)
                except Exception:
                    pass

            try:
                await bot.edit_message_text(f"⚙️ <b>Processing complete!</b>\nSize: <i>{size_mb:.1f} MB</i>", chat_id, status_id, parse_mode="HTML")
            except:
                pass

            if size_mb > MAX_SEND_MB:
                html_path = os.path.join(TMP_DIR, f"{short_hash(url)}.html")
                try:
                    with open(html_path, "w", encoding="utf-8") as fh:
                        fh.write(f"<html><body><h3>Download File</h3><p>Original: <a href=\"{url}\">{url}</a></p></body></html>")
                    try:
                        await bot.edit_message_text("❌ <b>File too large!</b> Failed to send\n<i>Sent fallback download page</i>", chat_id, status_id, parse_mode="HTML")
                    except:
                        pass
                    with open(html_path, "rb") as fh:
                        await bot.send_document(chat_id, fh, reply_to_message_id=reply_to_user_msgid or status_id, caption="⚠️ File >50MB — open this page to download manually")
                finally:
                    try: os.remove(html_path)
                    except: pass
            else:
                try:
                    await bot.edit_message_text("📤 <b>Sending directly...</b>", chat_id, status_id, parse_mode="HTML")
                except:
                    pass
                with open(final_path, "rb") as fh:
                    title = info.get("title", "Your file")
                    if media_type == "audio":
                        await bot.send_audio(chat_id, fh, reply_to_message_id=reply_to_user_msgid or status_id, caption=f"🎵 <b>{title}</b> — TB_Loader Pro+", parse_mode="HTML")
                    else:
                        await bot.send_video(chat_id, fh, supports_streaming=True, reply_to_message_id=reply_to_user_msgid or status_id, caption=f"🎬 <b>{title}</b> — TB_Loader Pro+", parse_mode="HTML")
                try:
                    await bot.edit_message_text("✅ <b>Sent successfully! Enjoy! 🎉</b>", chat_id, status_id, parse_mode="HTML")
                except:
                    pass

            uid = str(user_id)
            ud = user_data.get(uid, {"downloads":0, "total_mb":0.0, "last_download": None})
            ud["downloads"] = ud.get("downloads", 0) + 1
            ud["total_mb"] = ud.get("total_mb", 0.0) + size_mb
            ud["last_download"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            user_data[uid] = ud
            save_usage() 

        except Exception as e:
            print(f"Worker {worker_id} error:", e)
            try:
                await bot.edit_message_text("❌ <b>Download failed!</b>\nTry again", chat_id, status_id, parse_mode="HTML")
            except:
                try:
                    await bot.send_message(chat_id, "❌ <b>Download failed!</b>\nTry again", parse_mode="HTML")
                except:
                    pass
        finally:
            try:
                for f in os.listdir(TMP_DIR):
                    if f.startswith(os.path.basename(tmp_base)) or f.startswith(f"m_{chat_id}_{status_id}"):
                        try: os.remove(os.path.join(TMP_DIR, f))
                        except: pass
            except Exception:
                pass
            try:
                if url_key in url_storage:
                    url_storage.pop(url_key, None)
            except:
                pass
            download_queue.task_done()

# ===== Background tmp cleaner =====
async def tmp_cleaner():
    while True:
        try:
            now = time.time()
            for f in os.listdir(TMP_DIR):
                path = os.path.join(TMP_DIR, f)
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < now - TMP_CLEAN_INTERVAL and (f.startswith("dl_") or f.endswith(".html") or f.endswith(".tmp")):
                        os.remove(path)
                except Exception:
                    pass
        except Exception as e:
            print("tmp_cleaner error:", e)
        await asyncio.sleep(TMP_CLEAN_INTERVAL)

# ===== Main =====
async def main():
    print("🚀 TB_LOADER PRO+ v3.2 — Starting...")
    workers = [asyncio.create_task(download_worker(i)) for i in range(MAX_WORKERS)]
    asyncio.create_task(tmp_cleaner())
    await bot.infinity_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("Main loop stopped:", e)
    finally:
        save_usage()


