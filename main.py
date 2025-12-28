#!/usr/bin/env python3
# TB_LOADER PRO+ (v3.2) — Inline Enhanced (Fixed thumbnail)
# Features: inline single-link edit, batch links, tmp cleaner, usage persist, thumbnail fix,
# HTML fallback removed (NOT needed now), ffmpeg detection, cookie fallback, cooldown, insta limit, graceful shutdown.
# ✅ NEW UPDATE: Always send direct download link (expires 5 min) + parallel send worker + inline download button + chrome intent
# ✅ FIXED: status edit stuck issue (global safe_edit), link handler missing responses, convert stuck messages fixed

import os
import time
import asyncio
import shutil
import hashlib
import json
import signal
import atexit
import secrets
from datetime import datetime, timezone

from dotenv import load_dotenv
import yt_dlp
import aiohttp
import aiofiles
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from keep_alive import keep_alive

# ===== Config =====
USAGE_FILE = "/mnt/data/usage.json"
INSTA_FILE = "/mnt/data/insta_usage.json"
URL_TTL_SECONDS = 60 * 60  # 1 hour
MAX_URL_STORAGE = 2000
MAX_WORKERS = 12
TMP_CLEAN_INTERVAL = 3600  # seconds
COOLDOWN_SECONDS = 3
MAX_INSTA_PER_DAY = 10
MAX_SEND_MB = 50
TMP_DIR = "/tmp"

# ✅ Direct Download Link Config
DOWNLOAD_LINK_TTL = 300  # 5 min

# ===== Load .env =====
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")  # set in .env
if not API_TOKEN:
    raise RuntimeError("API_TOKEN not found in .env!")

bot = AsyncTeleBot(API_TOKEN)

# ===== Globals =====
FFMPEG_EXISTS = shutil.which("ffmpeg") is not None
download_queue = asyncio.Queue(maxsize=500)
insta_usage = {}
user_data = {}
lock = asyncio.Lock()
url_storage = {}
cooldown = {}

# ✅ Shared dict for keep_alive direct download links
download_links = {}
keep_alive(download_links, ttl=DOWNLOAD_LINK_TTL)


# ==========================================================
# ✅ SAFE EDIT HELPER (FIXES "status update stuck" everywhere)
# ==========================================================
async def safe_edit(chat_id, msg_id, text, parse_mode="HTML", reply_markup=None):
    """
    Try edit message. If edit fails -> send new message and return new msg_id
    This prevents old stuck "Generating..." texts.
    """
    try:
        await bot.edit_message_text(text, chat_id, msg_id, parse_mode=parse_mode, reply_markup=reply_markup)
        return msg_id
    except Exception:
        try:
            sent = await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
            return sent.message_id
        except Exception:
            return msg_id


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

def cleanup_url_storage():
    now = time.time()
    to_del = [k for k,v in url_storage.items() if now - v.get("created_at",0) > URL_TTL_SECONDS]
    for k in to_del:
        url_storage.pop(k, None)

def make_download_token():
    return secrets.token_urlsafe(16)

def register_download_link(filepath: str):
    token = make_download_token()
    download_links[token] = {
        "path": filepath,
        "expires": time.time() + DOWNLOAD_LINK_TTL
    }
    if PUBLIC_URL:
        return token, f"{PUBLIC_URL}/d/{token}"
    return token, f"/d/{token}"


# ===================================================
# ✅ MENU KEYBOARDS (UNCHANGED)
# ===================================================
async def send_start_keyboard(chat_id, msg_id=None):
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("📄 Profile", callback_data="profile"),
        InlineKeyboardButton("ℹ️ Help", callback_data="help"),
        InlineKeyboardButton("📝 About", callback_data="about"),
        InlineKeyboardButton("🎵 Convert Audio", callback_data="convert")
    )

    msg = (
        "🚀 <b>TB_LOADER v 4.0 PRO</b> — Fast Downloader\n\n"
        "💎 Supports: <b>Instagram</b> • <b>Twitter/X</b> • <b>Facebook</b> • <b>TikTok</b>\n"
        "🎬 Video & 🎵 Audio in seconds\n"
        f"⚠️ <i>Files up to {MAX_SEND_MB}MB</i>\n\n"
        "📩 <b>Paste one or more links below (space/newline separated)</b>\n\n"
        "<b>Download files with fast experience</b>⚡⚡"
    )
    if msg_id:
        await safe_edit(chat_id, msg_id, msg, reply_markup=markup)
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
        await safe_edit(chat_id, msg_id, msg, reply_markup=markup)
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
        "• Use /profile to see your usage\n\n"
        f"Limits: Instagram {MAX_INSTA_PER_DAY}/day per user. Cooldown: {COOLDOWN_SECONDS}s per user."
    )
    if msg_id:
        await safe_edit(chat_id, msg_id, msg, reply_markup=markup)
    else:
        sent = await bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return sent.message_id

async def send_about_keyboard(chat_id, msg_id=None):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🏠 Start", callback_data="start"),
        InlineKeyboardButton("ℹ️ Help", callback_data="help")
    )
    msg = "🚀 <b>TB_LOADER</b> — ✨ <i>Developed by</i> <b>MASHRAFI HAQUE</b> ✨\n🛠 <b>Version:</b> `v4.0` 🔥"
    if msg_id:
        await safe_edit(chat_id, msg_id, msg, reply_markup=markup)
    else:
        sent = await bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return sent.message_id

async def send_convert_audio_keyboard(chat_id, msg_id=None):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🏠 Start", callback_data="start"),
        InlineKeyboardButton("📄 Profile", callback_data="profile")
    )
    msg = (
        "🎵 <b>Convert Audio</b>\n\n"
        "Send me a video file, and I will convert it to audio (MP3) for you.\n\n"
        "• Works with videos up to 50MB\n"
        "• Use /start to return to main menu"
    )
    if msg_id:
        await safe_edit(chat_id, msg_id, msg, reply_markup=markup)
    else:
        sent = await bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return sent.message_id


# ===================================================
# ✅ BOT COMMANDS (UNCHANGED)
# ===================================================
@bot.message_handler(commands=["start"])
async def start(m):
    await send_start_keyboard(m.chat.id)

@bot.message_handler(commands=["profile"])
async def profile(m):
    await send_profile_keyboard(m.chat.id, m.from_user.id)

@bot.message_handler(commands=["help"])
async def help_cmd(m):
    await send_help_keyboard(m.chat.id)

@bot.message_handler(commands=["about"])
async def about(m):
    await send_about_keyboard(m.chat.id)

@bot.message_handler(commands=["convert"])
async def convert_audio(m):
    await send_convert_audio_keyboard(m.chat.id)


@bot.callback_query_handler(func=lambda c: c.data in ["start","profile","help","about","convert"])
async def inline_commands(call):
    await bot.answer_callback_query(call.id)
    cmd = call.data
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    msg_id = call.message.message_id

    if cmd == "start":
        await send_start_keyboard(chat_id, msg_id)
    elif cmd == "profile":
        await send_profile_keyboard(chat_id, user_id, msg_id)
    elif cmd == "help":
        await send_help_keyboard(chat_id, msg_id)
    elif cmd == "about":
        await send_about_keyboard(chat_id, msg_id)
    elif cmd == "convert":
        await send_convert_audio_keyboard(chat_id, msg_id)


# ===================================================
# ✅ LINK WORKER (INLINE BUTTON FIXED + CHROME)
# ===================================================
async def link_worker(chat_id, reply_to_msgid, final_path, size_mb, info):
    try:
        token, link = register_download_link(final_path)
        title = info.get("title", "Your file")

        # ✅ short url (hide your domain)
        short_link = await shorten_url(link)

        msg = (
            f"🔗 <b>Direct Download Link</b>\n"
            f"📌 <b>{title}</b>\n"
            f"📦 Size: <i>{size_mb:.1f} MB</i>\n"
            f"⏳ Valid: <i>{DOWNLOAD_LINK_TTL//60} min</i>\n\n"
            f"✅ Copy link below (tap to copy):\n"
            f"<code>{short_link}</code>"
        )

        await bot.send_message(
            chat_id,
            msg,
            parse_mode="HTML",
            reply_to_message_id=reply_to_msgid,
            disable_web_page_preview=True
        )

    except Exception as e:
        print("link_worker error:", e)

        # ✅ fallback (if shortener fails)
        try:
            await bot.send_message(
                chat_id,
                f"🔗 Download link:\n<code>{link}</code>",
                parse_mode="HTML",
                reply_to_message_id=reply_to_msgid,
                disable_web_page_preview=True
            )
        except:
            pass



# ===================================================
# ✅ SEND WORKER (UNCHANGED LOGIC, ONLY STATUS SAFE)
# ===================================================
async def send_worker(chat_id, status_id, reply_to_msgid, final_path, size_mb, info, media_type):
    try:
        if size_mb > MAX_SEND_MB:
            await bot.send_message(
                chat_id,
                f"❌ <b>File too large for Telegram!</b>\nSize: <i>{size_mb:.1f} MB</i>\n✅ Use download link above 👆",
                parse_mode="HTML",
                reply_to_message_id=reply_to_msgid or status_id
            )
            return

        status_id = await safe_edit(chat_id, status_id, "📤 <b>Sending directly...</b>")

        with open(final_path, "rb") as fh:
            title = info.get("title", "Your file")
            if media_type == "audio":
                await bot.send_audio(
                    chat_id, fh,
                    reply_to_message_id=reply_to_msgid or status_id,
                    caption=f"🎵 <b>{title}</b> — \n<b>TB_Loader</b>",
                    parse_mode="HTML"
                )
            else:
                await bot.send_video(
                    chat_id, fh,
                    supports_streaming=True,
                    reply_to_message_id=reply_to_msgid or status_id,
                    caption=f"🎬 <b>{title}</b> — \n<b>TB_Loader</b>",
                    parse_mode="HTML"
                )

        await safe_edit(chat_id, status_id, "✅ <b>Sent successfully! Enjoy! 🎉</b>")

    except Exception as e:
        print("send_worker error:", e)


# ===================================================
# ✅ VIDEO->AUDIO FEATURE (FIXED STATUS STUCK)
# ===================================================
@bot.message_handler(content_types=["video"])
async def handle_video_file(message):
    file_info = message.video
    file_id = file_info.file_id

    key = short_hash(file_id + str(time.time()))
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🎵 Convert to Audio (MP3)", callback_data=f"convert_{key}"))

    url_storage[key] = {
        "file_id": file_id,
        "chat_id": message.chat.id,
        "file_name": getattr(file_info, "file_name", "video")
    }

    status_msg = await bot.send_message(
        message.chat.id,
        "✅ Video received! Tap below to convert to audio:",
        reply_markup=markup
    )
    url_storage[key]["status_msg_id"] = status_msg.message_id


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("convert_"))
async def handle_convert_callback(call):
    await bot.answer_callback_query(call.id)
    key = call.data.split("_", 1)[1]
    rec = url_storage.get(key)
    if not rec:
        await bot.send_message(call.message.chat.id, "❌ Video expired or missing!")
        return

    chat_id = rec["chat_id"]
    file_id = rec["file_id"]
    file_name = rec.get("file_name", "video")
    msg_id = rec.get("status_msg_id")

    tmp_file = os.path.join(TMP_DIR, f"{key}.mp4")
    output_file = os.path.join(TMP_DIR, f"{key}.mp3")

    try:
        msg_id = await safe_edit(chat_id, msg_id, "⏳ <b>Downloading video...</b>")

        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path
        file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file_path}"

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status == 200:
                    async with aiofiles.open(tmp_file, mode="wb") as f:
                        await f.write(await resp.read())
                else:
                    raise Exception(f"Failed to download, status {resp.status}")

        msg_id = await safe_edit(chat_id, msg_id, "🎛 <b>Converting to MP3...</b>")

        if FFMPEG_EXISTS:
            cmd = f'ffmpeg -y -i "{tmp_file}" -vn -ab 192k -ar 44100 -f mp3 "{output_file}"'
            os.system(cmd)
        else:
            await safe_edit(chat_id, msg_id, "⚠️ FFmpeg not installed. Cannot convert.")
            return

        msg_id = await safe_edit(chat_id, msg_id, "📤 <b>Sending audio...</b>")

        async with aiofiles.open(output_file, "rb") as f:
            await bot.send_audio(chat_id, f, caption=f"🎵 {file_name} — Converted to MP3")

        await safe_edit(chat_id, msg_id, "✅ <b>Conversion complete!</b> 🎉")

    except Exception as e:
        print("Conversion error:", e)
        await safe_edit(chat_id, msg_id, "❌ <b>Conversion failed!</b>")
    finally:
        for f in [tmp_file, output_file]:
            try: os.remove(f)
            except: pass
        url_storage.pop(key, None)


# ===================================================
# ✅ TEXT HANDLER FIXED (LINK দিলে bot response করবে)
# ===================================================
@bot.message_handler(content_types=["text"])
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
            sent = await bot.send_message(
                message.chat.id,
                f"✅ <b>{pmap[platform]}</b> Detected!\n<i>Choose format below 👇</i>",
                reply_markup=markup,
                parse_mode="HTML"
            )
        else:
            sent = await bot.reply_to(
                message,
                f"✅ <b>{pmap[platform]}</b> Detected!\n<i>Choose format below 👇</i>",
                reply_markup=markup,
                parse_mode="HTML"
            )

        url_storage[key] = {
            "url": url,
            "created_at": time.time(),
            "platform": platform,
            "msg_id": sent.message_id,
            "inline": single,
            "orig_msg_id": message.message_id
        }

    if len(url_storage) > MAX_URL_STORAGE:
        cleanup_url_storage()


# ===================================================
# ✅ CALLBACK HANDLER (UNCHANGED)
# ===================================================
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith(("v_","a_")))
async def handle_callback(call):
    await bot.answer_callback_query(call.id)
    try:
        prefix = call.data[0]
        rest = call.data[2:]
        key, platform, orig_msgid = rest.rsplit("_", 2)

        rec = url_storage.get(key)
        if not rec:
            await bot.send_message(call.message.chat.id, "❌ <b>Link expired!</b> Send again.", parse_mode="HTML")
            return

        url = rec["url"]
        media_type = "video" if prefix == "v" else "audio"
        msg_id_to_edit = rec.get("msg_id")
        chat_id = call.message.chat.id

        msg_id_to_edit = await safe_edit(chat_id, msg_id_to_edit, "⏳ <b>Starting download...</b>\n⚡ <i>Processing</i>")

        await download_queue.put(
            (chat_id, url, platform, msg_id_to_edit, call.from_user.id, media_type, rec.get("orig_msg_id", None), key)
        )
    except Exception as e:
        print("Callback error:", e)
        await bot.send_message(call.message.chat.id, "❌ <b>Error!</b> Try again.", parse_mode="HTML")


# ===================================================
# ✅ DOWNLOAD WORKER (LINK FIRST + FINAL STATUS UPDATE FIXED)
# ===================================================
async def download_worker(worker_id: int):
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

            size_mb = os.path.getsize(final_path) / (1024 * 1024)

            status_id = await safe_edit(
                chat_id, status_id,
                f"⚙️ <b>Processing complete!</b>\nSize: <i>{size_mb:.1f} MB</i>\n🔗 Generating link..."
            )

            # ✅ link first, then send
            await link_worker(chat_id, reply_to_user_msgid or status_id, final_path, size_mb, info)
            await send_worker(chat_id, status_id, reply_to_user_msgid, final_path, size_mb, info, media_type)

            # ✅ final status update so old text doesn't stay
            if size_mb > MAX_SEND_MB:
                await safe_edit(chat_id, status_id,
                    "✅ <b>Link generated!</b>\n⚠️ <i>File too large for Telegram</i>\n⬇️ Use the download button above 👆"
                )
            else:
                await safe_edit(chat_id, status_id,
                    "✅ <b>Completed!</b>\n🔗 Link sent ✅\n📤 File sent successfully 🎉"
                )

            # usage stats
            uid = str(user_id)
            ud = user_data.get(uid, {"downloads": 0, "total_mb": 0.0, "last_download": None})
            ud["downloads"] += 1
            ud["total_mb"] += size_mb
            ud["last_download"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            user_data[uid] = ud
            save_usage()

        except Exception as e:
            print(f"Worker {worker_id} error:", e)
            await safe_edit(chat_id, status_id, "❌ <b>Download failed!</b>\nTry again")
        finally:
            url_storage.pop(url_key, None)
            download_queue.task_done()


# ===================================================
# ✅ TMP CLEANER (EXPIRED LINK FILES DELETE)
# ===================================================
async def tmp_cleaner():
    while True:
        try:
            now = time.time()

            # remove expired link files
            for token, rec in list(download_links.items()):
                if time.time() > rec.get("expires", 0):
                    try:
                        if os.path.exists(rec["path"]):
                            os.remove(rec["path"])
                    except:
                        pass
                    download_links.pop(token, None)

            # clean other tmp files (not active link files)
            for f in os.listdir(TMP_DIR):
                path = os.path.join(TMP_DIR, f)
                try:
                    active = any(rec.get("path") == path and time.time() <= rec.get("expires", 0)
                                 for rec in download_links.values())
                    if active:
                        continue

                    if os.path.isfile(path) and os.path.getmtime(path) < now - TMP_CLEAN_INTERVAL and (
                        f.startswith("dl_") or f.endswith(".tmp") or f.endswith(".html")
                    ):
                        os.remove(path)
                except Exception:
                    pass

        except Exception as e:
            print("tmp_cleaner error:", e)

        await asyncio.sleep(TMP_CLEAN_INTERVAL)


# ===================================================
# ✅ MAIN
# ===================================================
async def main():
    print("🚀 TB_LOADER PRO+ v3.2 — Starting...")
    workers = [asyncio.create_task(download_worker(i)) for i in range(MAX_WORKERS)]
    asyncio.create_task(tmp_cleaner())
    asyncio.create_task(auto_save_loop())
    await bot.infinity_polling()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("Main loop stopped:", e)
    finally:
        save_usage()


