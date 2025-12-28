#!/usr/bin/env python3
# TB_LOADER PRO+ (v3.2) — Inline Enhanced (Fixed thumbnail)
# ✅ MODIFIED:
# - YouTube removed (already)
# - /mnt/data -> /tmp/data (Render compatible)
# - Parallel workers:
#   (1) link_worker sends direct download link FAST
#   (2) download_worker downloads with yt-dlp and sends if <=50MB

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

from keep_alive import keep_alive
keep_alive()  # Flask server for uptime

# ===== Config =====
DATA_DIR = "/tmp/data"
os.makedirs(DATA_DIR, exist_ok=True)

USAGE_FILE = f"{DATA_DIR}/usage.json"
INSTA_FILE = f"{DATA_DIR}/insta_usage.json"

URL_TTL_SECONDS = 60 * 60  # 1 hour
MAX_URL_STORAGE = 2000
MAX_WORKERS = 12
MAX_LINK_WORKERS = 3   # ✅ fast worker for link extraction
TMP_CLEAN_INTERVAL = 3600
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
link_queue = asyncio.Queue(maxsize=500)  # ✅ new queue for direct link worker
insta_usage = {}
user_data = {}
lock = asyncio.Lock()
url_storage = {}   # key -> {url, created_at, platform, msg_id, inline, orig_msg_id, link_sent}
cooldown = {}


# ===== Persistent usage load/save =====
def load_usage():
    global user_data, insta_usage
    os.makedirs(DATA_DIR, exist_ok=True)

    try:
        if os.path.exists(USAGE_FILE):
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                user_data = json.load(f)
        else:
            user_data = {}
    except Exception as e:
        print("load_usage error:", e)
        user_data = {}

    try:
        if os.path.exists(INSTA_FILE):
            with open(INSTA_FILE, "r", encoding="utf-8") as f:
                insta_usage = json.load(f)
        else:
            insta_usage = {}
    except Exception as e:
        print("load_insta_usage error:", e)
        insta_usage = {}


def save_usage():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f)
    except Exception as e:
        print("save_usage error:", e)

    try:
        with open(INSTA_FILE, "w", encoding="utf-8") as f:
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
    to_del = [k for k, v in url_storage.items() if now - v.get("created_at", 0) > URL_TTL_SECONDS]
    for k in to_del:
        url_storage.pop(k, None)


# ===== Inline Keyboard Command Helpers (EDIT IN PLACE) =====
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
        "• Use /profile to see your usage\n\n"
        f"Limits: Instagram {MAX_INSTA_PER_DAY}/day per user. Cooldown: {COOLDOWN_SECONDS}s per user."
    )
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
    msg = "🚀 <b>TB_LOADER</b> — ✨ <i>Developed by</i> <b>MASHRAFI HAQUE</b> ✨\n🛠 <b>Version:</b> `v4.0` 🔥"
    if msg_id:
        await bot.edit_message_text(msg, chat_id, msg_id, parse_mode="HTML", reply_markup=markup)
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
        try:
            await bot.edit_message_text(msg, chat_id, msg_id, parse_mode="HTML", reply_markup=markup)
        except:
            sent = await bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
            return sent.message_id
    else:
        sent = await bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return sent.message_id


# ===== Bot commands =====
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

    links = [l.strip() for l in text.split() if l.strip().startswith(("http://", "https://"))]
    if not links:
        await bot.reply_to(message, "❌ <b>No valid link found!</b> Send Instagram/Twitter/Facebook/TikTok link 🔗", parse_mode="HTML")
        return

    single = len(links) == 1
    pmap = {"instagram": "Instagram", "twitter": "Twitter/X", "facebook": "Facebook", "tiktok": "TikTok"}

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
            "orig_msg_id": message.message_id,
            "link_sent": False
        }

    if len(url_storage) > MAX_URL_STORAGE:
        cleanup_url_storage()


# ===== Callback handler =====
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith(("v_", "a_")))
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

        try:
            await bot.edit_message_text("⏳ <b>Starting download...</b>\n⚡ <i>Processing</i>", chat_id, msg_id_to_edit, parse_mode="HTML")
        except:
            status_msg = await bot.send_message(chat_id, "⏳ <b>Starting download...</b>\n⚡ <i>Processing</i>", parse_mode="HTML")
            msg_id_to_edit = status_msg.message_id
            rec["msg_id"] = msg_id_to_edit

        # ✅ Parallel work: enqueue both
        job = (chat_id, url, platform, msg_id_to_edit, call.from_user.id, media_type, rec.get("orig_msg_id", None), key)
        await link_queue.put(job)      # fast direct link
        await download_queue.put(job)  # slow download + send if <=50MB

    except Exception as e:
        print("Callback error:", e)
        await bot.send_message(call.message.chat.id, "❌ <b>Error!</b> Try again.", parse_mode="HTML")


# ✅ FAST worker: send direct download link immediately
async def link_worker(worker_id: int):
    while True:
        chat_id, url, platform, status_id, user_id, media_type, reply_to_user_msgid, url_key = await link_queue.get()
        try:
            rec = url_storage.get(url_key, {})
            if rec.get("link_sent"):
                link_queue.task_done()
                continue

            # extract direct url (no download)
            opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
            }

            # for audio try to prefer bestaudio
            if media_type == "audio":
                opts["format"] = "bestaudio/best"
            else:
                opts["format"] = "best[ext=mp4]/best"

            direct_url = None
            title = None

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = await asyncio.to_thread(ydl.extract_info, url, download=False)

                if isinstance(info, dict):
                    title = info.get("title")
                    direct_url = info.get("url")

                    if not direct_url:
                        rfs = info.get("requested_formats") or []
                        if isinstance(rfs, list) and len(rfs) > 0:
                            direct_url = rfs[0].get("url")

            except Exception as e:
                print("link_worker extract error:", e)

            if direct_url:
                rec["link_sent"] = True
                url_storage[url_key] = rec

                # shorten for display only
                short = await shorten_url(direct_url)

                await bot.send_message(
                    chat_id,
                    f"⚡ <b>Instant Download Link Ready</b>\n"
                    f"📌 {('🎵 Audio' if media_type=='audio' else '🎬 Video')}\n"
                    f"{f'🎞 <b>{title}</b>\\n' if title else ''}"
                    f"🔗 <code>{short}</code>\n\n"
                    f"✅ Copy this link & open in browser to download.\n"
                    f"⏳ Meanwhile, bot will try to send file if under {MAX_SEND_MB}MB.",
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
            else:
                # fallback: send original url
                if not rec.get("link_sent"):
                    rec["link_sent"] = True
                    url_storage[url_key] = rec
                    await bot.send_message(
                        chat_id,
                        "⚠️ <b>Direct link not available now</b>\n"
                        "✅ Please open the original link:\n"
                        f"<code>{url}</code>",
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )

        except Exception as e:
            print("link_worker error:", e)
        finally:
            link_queue.task_done()


# ===== Download Worker (same as your old logic, only HTML fallback optional) =====
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
                # ✅ safer on render without ffmpeg merge
                ydl_opts["format"] = "best[ext=mp4]/best"

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

            thumb = None
            if media_type == "audio" and isinstance(info, dict):
                thumb = info.get("thumbnail")
            if thumb:
                try:
                    reply_to = reply_to_user_msgid or status_id
                    await bot.send_photo(chat_id, thumb, reply_to_message_id=reply_to)
                except:
                    pass

            try:
                await bot.edit_message_text(
                    f"⚙️ <b>Processing complete!</b>\nSize: <i>{size_mb:.1f} MB</i>",
                    chat_id, status_id, parse_mode="HTML"
                )
            except:
                pass

            # ✅ if big, just inform (link already sent by link_worker)
            if size_mb > MAX_SEND_MB:
                try:
                    await bot.edit_message_text(
                        f"⚠️ <b>File too large for Telegram</b>\n"
                        f"📦 Size: <b>{size_mb:.1f} MB</b>\n\n"
                        f"✅ I already sent you an instant download link above.",
                        chat_id, status_id, parse_mode="HTML"
                    )
                except:
                    pass
            else:
                try:
                    await bot.edit_message_text("📤 <b>Sending directly...</b>", chat_id, status_id, parse_mode="HTML")
                except:
                    pass

                with open(final_path, "rb") as fh:
                    title = info.get("title", "Your file")
                    if media_type == "audio":
                        await bot.send_audio(chat_id, fh, reply_to_message_id=reply_to_user_msgid or status_id,
                                             caption=f"🎵 <b>{title}</b> — \n<b>TB_Loader</b>", parse_mode="HTML")
                    else:
                        await bot.send_video(chat_id, fh, supports_streaming=True,
                                             reply_to_message_id=reply_to_user_msgid or status_id,
                                             caption=f"🎬 <b>{title}</b> — \n<b>TB_Loader</b>", parse_mode="HTML")

                try:
                    await bot.edit_message_text("✅ <b>Sent successfully! Enjoy! 🎉</b>", chat_id, status_id, parse_mode="HTML")
                except:
                    pass

            uid = str(user_id)
            ud = user_data.get(uid, {"downloads": 0, "total_mb": 0.0, "last_download": None})
            ud["downloads"] += 1
            ud["total_mb"] += size_mb
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
                        try:
                            os.remove(os.path.join(TMP_DIR, f))
                        except:
                            pass
            except:
                pass

            try:
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
                    if os.path.isfile(path) and os.path.getmtime(path) < now - TMP_CLEAN_INTERVAL and (
                        f.startswith("dl_") or f.endswith(".html") or f.endswith(".tmp")
                    ):
                        os.remove(path)
                except:
                    pass
        except Exception as e:
            print("tmp_cleaner error:", e)
        await asyncio.sleep(TMP_CLEAN_INTERVAL)


# ===== Main =====
async def main():
    print("🚀 TB_LOADER PRO+ v3.2 — Starting...")

    # download workers
    workers = [asyncio.create_task(download_worker(i)) for i in range(MAX_WORKERS)]
    # link workers (fast)
    link_workers = [asyncio.create_task(link_worker(i)) for i in range(MAX_LINK_WORKERS)]

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
