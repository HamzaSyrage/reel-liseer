import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
import re

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ReplyParameters, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler

from reel_liseer import config
from reel_liseer.services.downloader import download, download_with_format, get_video_formats
from reel_liseer.services.media import cleanup_downloads, make_thumbnail, probe as media_probe, to_telegram_safe

URL_REGEX = r"^https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{2,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)$"
URL_PATTERN = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
SOCIAL_REGEX = (
    r"https?://(?:www\.)?"
    r"(?:"
    r"youtube\.com/(?:watch/?\?|embed/|shorts/|live/|v/|playlist\?)"
    r"|youtu\.be/"
    r"|facebook\.com/(?:watch/?\?|reel/|reels/|share/v/|share/r/)"
    r"|instagram\.com/(?:p|reel|reels|tv|stories)/"
    r"|tiktok\.com/@[\w.-]+/(?:video/)?"
    r"|(?:twitter|x)\.com/[\w.-]+/status(?:es)?/"
    r"|reddit\.(?:com|it)/r/"
    r"|(?:i\.)?redd\.it/"
    r"|(?:[\w-]+\.)?redditmedia\.com/"
    r"|(?:[\w-]+\.)?redditstatic\.com/"
    r"|media\.tenor\.(?:com|co)/"
    r"|(?:media|i)\.giphy\.com/"
    r"|i\.imgur\.com/"
    r")"
    r"[\w./?=&%#:+~-]+"
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
    """Anchor a send to the message that triggered it.

    ``allow_sending_without_reply`` keeps the send working when the anchor is already
    gone: the bot deletes its own messages (format menus, transient notices) and the
    Bot API rejects the whole request otherwise.
    """
    if not message_id:
        return None
    return ReplyParameters(message_id=message_id, allow_sending_without_reply=True)

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

# async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     try:
#         await update.message.reply_text("Starting YouTube test...")
#
#         output_path = "/app/src/reel_liseer/.downloaded-media/test-youtube"
#
#         ydl_opts = {
#             "cookiefile": "/app/src/youtube-cookies.txt",
#             "format": "best[ext=mp4]/best",
#             "outtmpl": f"{output_path}.%(ext)s",
#             "verbose": True,
#             "extractor_args" : {
#                 'youtube': {
#                     'player_client': ['default', 'web_embedded']
#                     }
#             }
#         }
#
#         def run_download():
#             with yt_dlp.YoutubeDL(ydl_opts) as ydl:
#                 info = ydl.extract_info(
#                     "https://www.youtube.com/watch?v=DogH-RgansQ",
#                     download=False
#                     )
#                 return ydl.prepare_filename(info)
#
#         downloaded_file = await asyncio.to_thread(run_download)
#
#         await update.message.reply_text(
#             f"Download succeeded:\n{downloaded_file}"
#         )
#
#     except Exception as e:
#         logger.exception("YouTube test failed")
#         await update.message.reply_text(
#             f"YouTube test failed:\n{type(e).__name__}: {e}"
#         )

async def deliver_media(
    bot,
    chat_id: int,
    media: dict,
    reply_to_message_id: int | None = None,
    message_thread_id: int | None = None,
):
    path = Path(media['path'])
    thumbnail = None
    transcode_to = None
    reply_parameters = reply_parameters_for(reply_to_message_id)

    try:
        info = await asyncio.to_thread(media_probe, path, media)

        if info.is_animation():
            # streamed from disk rather than read into memory
            with open(path, "rb") as animation_file:  # noqa: ASYNC230
                await bot.send_animation(
                    chat_id=chat_id,
                    animation=InputFile(
                        animation_file, filename=path.name, read_file_handle=False
                    ),
                    duration=info.duration,
                    width=info.width,
                    height=info.height,
                    message_thread_id=message_thread_id,
                    reply_parameters=reply_parameters,
                )
            return

        if info.needs_transcode():
            transcode_to = await asyncio.to_thread(to_telegram_safe, path)
            path = transcode_to
            info = await asyncio.to_thread(media_probe, path, {})

        thumbnail = await asyncio.to_thread(make_thumbnail, path)

        # caption = (media.get('title') or '')[:1024] or None

        with open(path, "rb") as video_file:  # noqa: ASYNC230
            await bot.send_video(
                chat_id=chat_id,
                video=InputFile(video_file, filename=path.name, read_file_handle=False),
                duration=info.duration,
                width=info.width,
                height=info.height,
                thumbnail=str(thumbnail) if thumbnail else None,
                supports_streaming=True,
                # caption=caption,
                message_thread_id=message_thread_id,
                reply_parameters=reply_parameters,
            )
    finally:
        if thumbnail:
            Path(thumbnail).unlink(missing_ok=True)  # noqa: ASYNC240
        if transcode_to:
            Path(transcode_to).unlink(missing_ok=True)  # noqa: ASYNC240
        Path(media['path']).unlink(missing_ok=True)  # noqa: ASYNC240

async def send_downloaded_file(update: Update, context: ContextTypes.DEFAULT_TYPE, link: str):
    async with JOB_SEMAPHORE:
        media = await asyncio.to_thread(download, link)
        await deliver_media(
            context.bot,
            update.effective_chat.id,
            media,
            update.message.message_id,
            update.message.message_thread_id,
        )

async def show_format_options(update: Update, context: ContextTypes.DEFAULT_TYPE, link: str):
    try:
        title, video_formats = await asyncio.to_thread(get_video_formats, link)

        user = update.effective_user
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{sanitize_filename(title)}_{timestamp}"

        if 'cache' not in context.user_data:
            context.user_data['cache'] = {}
        # The keyboard message is deleted when a button is tapped, so remember the
        # message that actually asked for the download to reply to later.
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

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
        user = query.from_user
        callback_data = query.data

        # Read the cache before deleting the keyboard message, otherwise the expiry
        # notice below has nothing left to reply to.
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

def build_application() -> Application:
    removed = cleanup_downloads()
    if removed:
        logger.info("Cleared %s leftover file(s) from %s", removed, config.DOWNLOAD_PATH)

    application = (
        Application.builder()
        .token(TOKEN)
        .request(HTTPXRequest(media_write_timeout=config.MEDIA_WRITE_TIMEOUT))
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    # application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("options", options_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_error_handler(error_handler)
    return application

def main() -> None:
    # import time
    # logger.info("Waiting 60 seconds for the bot to be ready...")
    # time.sleep(60)
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