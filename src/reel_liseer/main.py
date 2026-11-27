import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
import re

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ReplyParameters, Update, BotCommand
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler

from reel_liseer import config
from reel_liseer.services.downloader import download, download_with_format, format_size, get_video_formats
from reel_liseer.services.media import cleanup_downloads, make_thumbnail, probe as media_probe, to_telegram_safe

URL_REGEX = r"^https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{2,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)$"
URL_PATTERN = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
# Every pattern is matched right after "https://(www.)?" and must be a site yt-dlp can handle.
SOCIAL_SITE_PATTERNS = [
    # YouTube (also m. and music.)
    r"(?:m\.|music\.)?youtube\.com/(?:watch/?\?|embed/|shorts/|live/|v/|playlist\?)",
    r"youtu\.be/",
    # Facebook / Instagram / TikTok / X
    r"(?:m\.|web\.)?facebook\.com/(?:watch/?\?|reel/|reels/|share/v/|share/r/|[\w.-]+/videos/)",
    r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv|stories)/",
    r"tiktok\.com/(?:@[\w.-]+/(?:video/)?|t/)",
    r"(?:vm|vt)\.tiktok\.com/",
    r"(?:mobile\.)?(?:twitter|x)\.com/[\w.-]+/status(?:es)?/",
    # Reddit
    r"reddit\.(?:com|it)/r/",
    r"(?:[iv]\.)?redd\.it/",
    r"(?:[\w-]+\.)?redditmedia\.com/",
    r"(?:[\w-]+\.)?redditstatic\.com/",
    # GIF hosts
    r"media\.tenor\.(?:com|co)/",
    r"(?:media|i)\.giphy\.com/",
    r"i\.imgur\.com/",
    r"imgur\.com/(?:a|gallery)/",
    # Video platforms
    r"(?:player\.)?vimeo\.com/",
    r"dailymotion\.com/video/",
    r"dai\.ly/",
    r"twitch\.tv/(?:videos/|[\w-]+/clip/)",
    r"clips\.twitch\.tv/",
    r"kick\.com/[\w-]+/clips/",
    r"streamable\.com/",
    r"rumble\.com/",
    r"odysee\.com/",
    r"bilibili\.com/video/",
    r"vk\.com/(?:video|clip)",
    r"coub\.com/view/",
    r"loom\.com/share/",
    r"9gag\.com/gag/",
    # Social / microblogs
    r"bsky\.app/profile/[\w.:-]+/post/",
    r"pinterest\.[a-z.]+/pin/",
    r"(?:[\w-]+\.)?tumblr\.com/(?:post/|[\w-]+/\d)",
    r"snapchat\.com/(?:spotlight|t)/",
]
SOCIAL_REGEX = (
    r"https?://(?:www\.)?(?:" + "|".join(SOCIAL_SITE_PATTERNS) + r")[\w./?=&%#:+~@,;-]+"
)

LONGFORM_YOUTUBE_REGEX = (
    r"^https?://(?:[a-z0-9-]+\.)?(?:"
    r"youtube\.com(?:/watch\?v=|/embed/|/v/|/)|youtu\.be/"
    r")([\w.-]+)"
)

def message_has_link(message):
    if not message:
        return False
    return bool(re.search(URL_PATTERN, message, re.IGNORECASE))

def extract_link_from_message(message):
    if not message or not message.text:
        return None
    match = re.findall(URL_PATTERN, message.text, re.IGNORECASE)
    return match[0] if match else None

def is_accepted_social_link(text):
    return isinstance(text, str) and bool(re.fullmatch(SOCIAL_REGEX, text, re.IGNORECASE))

def is_a_longform_youtube_link(text):
    if not isinstance(text, str):
        return False
    is_youtube = bool(re.match(LONGFORM_YOUTUBE_REGEX, text, re.IGNORECASE))
    is_shorts = "/shorts/" in text.lower()
    return is_youtube and not is_shorts

def sanitize_filename(name):
    return re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')

def reply_parameters_for(message_id: int | None) -> ReplyParameters | None:
    if not message_id:
        return None
    return ReplyParameters(message_id=message_id, allow_sending_without_reply=True)

CAPTION_MAX = 1024

def format_duration(seconds) -> str:
    seconds = int(seconds or 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"

def build_verbose_caption(media: dict, info, path: Path) -> str:
    lines = []
    if media.get('title'):
        lines.append(f"Title: {media['title']}")
    if media.get('uploader'):
        lines.append(f"Uploader: {media['uploader']}")
    if info.duration:
        lines.append(f"Duration: {format_duration(info.duration)}")
    if info.width and info.height:
        lines.append(f"Resolution: {info.width}x{info.height}")
    try:
        lines.append(f"Size: {format_size(path.stat().st_size)}")
    except OSError:
        pass
    if media.get('webpage_url'):
        lines.append(f"Link: {media['webpage_url']}")
    return "\n".join(lines)[:CAPTION_MAX] or None

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TELEGRAM_TEXT_MAX = 4096

def format_error_report(link: str, step: str, exc: BaseException) -> str:
    detail = ANSI_RE.sub("", str(exc)).strip() or "(no message)"
    report = (
        "Error while processing your link\n"
        f"Step: {step}\n"
        f"Link: {link}\n"
        f"{type(exc).__name__}: {detail}"
    )
    return report[:TELEGRAM_TEXT_MAX]

def delete_after(message, seconds):
    async def delete_message():
        await asyncio.sleep(seconds)
        try:
            await message.delete()
        except Exception:
            logger.exception("Could not delete message after timeout")
    asyncio.create_task(delete_message())


load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

import logging

from telegram.ext import Application, filters
from telegram.request import HTTPXRequest

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# heavy work is queued
JOB_SEMAPHORE = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        await update.message.reply_html(rf"Hi {user.mention_html()}!")
    except Exception:
        logger.exception("Error in start handler")

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.reply_text("Pong!")
    except Exception:
        logger.exception("Error in ping handler")

async def deliver_media(
    bot,
    chat_id: int,
    media: dict,
    reply_to_message_id: int | None = None,
    message_thread_id: int | None = None,
    verbose: bool = False,
):
    path = Path(media['path'])
    thumbnail = None
    transcode_to = None
    reply_parameters = reply_parameters_for(reply_to_message_id)
    step = "checking downloaded file"

    try:
        if not path.is_file() or path.stat().st_size == 0:  # noqa: ASYNC240
            raise RuntimeError(f"downloaded file is missing or empty: {path}")

        step = "reading media info"
        info = await asyncio.to_thread(media_probe, path, media)

        if info.is_animation():
            caption = build_verbose_caption(media, info, path) if verbose else None
            step = "uploading animation to Telegram"
            with open(path, "rb") as animation_file:  # noqa: ASYNC230
                await bot.send_animation(
                    chat_id=chat_id,
                    animation=InputFile(
                        animation_file, filename=path.name, read_file_handle=False
                    ),
                    duration=info.duration,
                    width=info.width,
                    height=info.height,
                    caption=caption,
                    message_thread_id=message_thread_id,
                    reply_parameters=reply_parameters,
                )
            return

        if info.needs_transcode():
            step = "converting video for Telegram (ffmpeg)"
            transcode_to = await asyncio.to_thread(to_telegram_safe, path)
            path = transcode_to
            info = await asyncio.to_thread(media_probe, path, {})

        step = "creating thumbnail"
        thumbnail = await asyncio.to_thread(make_thumbnail, path)

        caption = build_verbose_caption(media, info, path) if verbose else None

        step = "uploading video to Telegram"
        with open(path, "rb") as video_file:  # noqa: ASYNC230
            await bot.send_video(
                chat_id=chat_id,
                video=InputFile(video_file, filename=path.name, read_file_handle=False),
                duration=info.duration,
                width=info.width,
                height=info.height,
                thumbnail=str(thumbnail) if thumbnail else None,
                supports_streaming=True,
                caption=caption,
                message_thread_id=message_thread_id,
                reply_parameters=reply_parameters,
            )
    except Exception as exc:
        exc.failed_step = step
        raise
    finally:
        if thumbnail:
            Path(thumbnail).unlink(missing_ok=True)  # noqa: ASYNC240
        if transcode_to:
            Path(transcode_to).unlink(missing_ok=True)  # noqa: ASYNC240
        Path(media['path']).unlink(missing_ok=True)  # noqa: ASYNC240

async def send_downloaded_file(
    update: Update, context: ContextTypes.DEFAULT_TYPE, link: str, verbose: bool = False
):
    async with JOB_SEMAPHORE:
        step = "downloading"
        try:
            media = await asyncio.to_thread(download, link)
            step = "sending"
            await deliver_media(
                context.bot,
                update.effective_chat.id,
                media,
                update.message.message_id,
                update.message.message_thread_id,
                verbose=verbose,
            )
        except Exception as exc:
            if not verbose:
                raise
            logger.exception("Error in verbose job for %s", link)
            step = getattr(exc, "failed_step", step)
            try:
                await update.message.reply_text(format_error_report(link, step, exc))
            except Exception:
                logger.exception("Could not send verbose error report")

async def show_format_options(update: Update, context: ContextTypes.DEFAULT_TYPE, link: str):
    try:
        title, video_formats = await asyncio.to_thread(get_video_formats, link)

        user = update.effective_user
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{sanitize_filename(title)}_{timestamp}"

        if 'cache' not in context.user_data:
            context.user_data['cache'] = {}
        context.user_data['cache'][user.id] = {
            "outtmpl": file_name,
            "url": link,
            "source_message_id": update.message.message_id,
            "source_thread_id": update.message.message_thread_id,
        }

        keyboard_markup = [
            [
                InlineKeyboardButton("Best Audio", callback_data="bestaudio"),
                InlineKeyboardButton("Best Video", callback_data="bestvideo"),
            ],
            *[
                [
                    InlineKeyboardButton(
                        text=f"{f['format']} {f['file_size'] and f['file_size'] or ''}",
                        callback_data=f"format_{f['format_id']}"
                    )
                ]
                for f in video_formats
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard_markup)
        await update.message.reply_text(text='select', reply_markup=reply_markup)
    except Exception:
        logger.exception("Error showing format options")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not message_has_link(update.message.text):
            return
        link = extract_link_from_message(update.message)
        if not is_accepted_social_link(link):
            # await update.message.reply_text("That doesn't look like a valid link on a supported platform.")
            return

        if context.user_data.get('waiting_a_url_for_verbose'):
            context.user_data['waiting_a_url_for_verbose'] = False

            message_id = context.user_data.pop('waiting_message_id', None)

            if message_id:
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=message_id,
                    )
                except Exception:
                    logger.exception("Could not delete waiting message")

            await send_downloaded_file(update, context, link, verbose=True)
            return

        if context.user_data.get('waiting_a_url_for_options'):
            context.user_data['waiting_a_url_for_options'] = False

            message_id = context.user_data.pop('waiting_message_id', None)

            if message_id:
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=message_id,
                    )
                except Exception:
                    logger.exception("Could not delete waiting message")

            await show_format_options(update, context, link)
            return

        if is_a_longform_youtube_link(link):
            await show_format_options(update, context, link)
            return

        await send_downloaded_file(update, context, link)
    except Exception:
        logger.exception("Error handling message")

async def options_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        parts = update.message.text.split(" ")
        if len(parts) < 2:
            message = await update.message.reply_text("send me the link in the next message")
            context.user_data['waiting_a_url_for_verbose'] = False
            context.user_data['waiting_a_url_for_options'] = True
            context.user_data['waiting_message_id'] = message.message_id
            return

        link = extract_link_from_message(update.message)
        if not link or not is_accepted_social_link(link):
            message = await update.message.reply_text("That doesn't look like a valid link on a supported platform.")
            # async 5 sec and then delete
            delete_after(message,5)
            return

        await show_format_options(update, context, link)
    except Exception:
        logger.exception("Error in options handler")

async def verbose_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            message = await update.message.reply_text("send me the link in the next message")
            context.user_data['waiting_a_url_for_options'] = False
            context.user_data['waiting_a_url_for_verbose'] = True
            context.user_data['waiting_message_id'] = message.message_id
            return

        link = extract_link_from_message(update.message)
        if not link or not is_accepted_social_link(link):
            message = await update.message.reply_text("That doesn't look like a valid link on a supported platform.")
            delete_after(message, 5)
            return

        await send_downloaded_file(update, context, link, verbose=True)
    except Exception:
        logger.exception("Error in verbose handler")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
        user = query.from_user
        callback_data = query.data

        entry = context.user_data.get('cache', {}).get(user.id)
        if entry is None:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Button expired (bot restarted). Please send the link again.",
                reply_parameters=reply_parameters_for(query.message.message_id),
            )
            await query.delete_message()
            return

        await query.delete_message()

        if callback_data == "bestaudio":
            format_str = "bestaudio[ext=m4a]/bestaudio"
        elif callback_data == "bestvideo":
            format_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4"
        elif callback_data.startswith("format_"):
            format_id = callback_data.removeprefix("format_")
            format_str = f"{format_id}+bestaudio[ext=m4a]/best"
        else:
            format_str = callback_data

        url = entry['url']
        outtmpl = entry['outtmpl']
        source_message_id = entry.get('source_message_id')
        source_thread_id = entry.get('source_thread_id')

        async with JOB_SEMAPHORE:
            media = await asyncio.to_thread(
                download_with_format, url=url, outtmpl=outtmpl, format=format_str
            )

            if not format_str.startswith('bestaudio'):
                await deliver_media(
                    context.bot,
                    query.message.chat_id,
                    media,
                    source_message_id,
                    source_thread_id,
                )
                return

            try:
                with open(media['path'], "rb") as audio_file:  # noqa: ASYNC230
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=InputFile(
                            audio_file, filename=Path(media['path']).name, read_file_handle=False
                        ),
                        title=media.get('title'),
                        message_thread_id=source_thread_id,
                        reply_parameters=reply_parameters_for(source_message_id),
                    )
            finally:
                Path(media['path']).unlink(missing_ok=True)  # noqa: ASYNC240
    except Exception:
        logger.exception("Error handling callback")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception", exc_info=context.error)

COMMANDS = [
    BotCommand("start", "to start the bot"),
    BotCommand("ping", "to ping the bot"),
    BotCommand("options", "<URL> to chose the download format"),
    # BotCommand("verbose", "<URL> to verbose the steps and errors"),
]

async def register_commands(application: Application) -> None:
    try:
        await application.bot.set_my_commands(COMMANDS)
    except Exception:
        logger.exception("Could not register bot commands")




def build_application() -> Application:
    removed = cleanup_downloads()
    if removed:
        logger.info("Cleared %s leftover file(s) from %s", removed, config.DOWNLOAD_PATH)

    request = HTTPXRequest(
        media_write_timeout=config.MEDIA_WRITE_TIMEOUT,
        read_timeout=config.MEDIA_WRITE_TIMEOUT,
        write_timeout=config.MEDIA_WRITE_TIMEOUT,
        connect_timeout=30,
        pool_timeout=30,
    )
    builder = Application.builder().token(TOKEN).request(request).post_init(register_commands).build()
    
    if config.TELEGRAM_API_BASE_URL:
        builder = (
            builder.base_url(f"{config.TELEGRAM_API_BASE_URL}/bot")
            .base_file_url(f"{config.TELEGRAM_API_BASE_URL}/file/bot")
            .local_mode(True)
        )
    application = builder.build()
    application.add_handler(CommandHandler("start", start))
    # application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler(["options", "o"], options_command))
    application.add_handler(CommandHandler(["verbose", "v"], verbose_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_error_handler(error_handler)
    return application

def main() -> None:
    mode = os.getenv("BOT_MODE", "polling").lower().strip()
    if mode == "webhook":
        import uvicorn
        port = int(os.getenv("PORT", "8000"))
        logger.info("Starting in webhook mode on port %s", port)
        from reel_liseer.webapp import app as fastapi_app
        uvicorn.run(fastapi_app, host="0.0.0.0", port=port)
    else:
        logger.info("Starting in polling mode")
        build_application().run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()