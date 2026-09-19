import asyncio
from datetime import datetime
from pathlib import Path
import time
import uuid

import yt_dlp

from reel_liseer.services.downloader import download, get_youtube_download_info, printing, youtube_download
from reel_liseer.config import DOWLOAD_PATH
import os
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes,MessageHandler

import re

URL_REGEX = r"^https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{2,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)$"

def is_valid_link(text): 
    return isinstance(text, str) and bool(re.match(URL_REGEX, text, re.IGNORECASE))

SOCIAL_REGEX = (
    r"^https?://(?:[a-z0-9-]+\.)?(?:"
    r"youtube\.com(?:/watch\?v=|/embed/|/shorts/|/)|youtu\.be/|"
    r"facebook\.com/|"
    r"instagram\.com/|"
    r"tiktok\.com/(?:@[\w.-]+/video/|@[\w.-]+/|)|"
    r"twitter\.com/|x\.com/"
    r")([\w.-]+)"
)

def is_accepted_social_link(text):
    return isinstance(text, str) and bool(re.match(SOCIAL_REGEX, text, re.IGNORECASE))

LONGFORM_YOUTUBE_REGEX = (
    r"^https?://(?:[a-z0-9-]+\.)?(?:"
    r"youtube\.com(?:/watch\?v=|/embed/|/v/|/|)|youtu\.be/"
    r")([\w.-]+)"
)

def is_a_longform_youtube_link(text):
    # Matches normal videos and explicitly filters out Shorts
    if not isinstance(text, str):
        return False
        
    is_youtube = bool(re.match(LONGFORM_YOUTUBE_REGEX, text, re.IGNORECASE))
    is_shorts = "/shorts/" in text.lower()
    
    return is_youtube and not is_shorts

load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# url = 'https://www.instagram.com/p/DMl54nhKNnA/'
# url = 'https://www.youtube.com/watch?v=Rc2k_8skxtI'
# download(url)
import logging

from telegram import ForceReply, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# Define a few command handlers. These usually take the two arguments update and
# context.
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    try:
        user = update.effective_user
        await update.message.reply_html(
            rf"Hi {user.mention_html()}!",
            # reply_markup=ForceReply(selective=True),
        )
    except Exception:
        logger.exception("Error in start handler")


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        start_time = time.time()
        await update.message.reply_text("Pong!")
        end_time = time.time()
        elapsed_time_ms = (end_time - start_time) * 1000
        await update.message.reply_text(f"Response time: {elapsed_time_ms:.2f} ms")
    except Exception:
        logger.exception("Error in ping handler")

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.reply_text("Starting YouTube test...")

        output_path = "/app/src/reel_liseer/.downloaded-media/test-youtube"

        ydl_opts = {
            "cookiefile": "/app/src/youtube-cookies.txt",
            "format": "best[ext=mp4]/best",
            "outtmpl": f"{output_path}.%(ext)s",
            "verbose": True,
        }

        def run_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    "https://www.youtube.com/watch?v=DogH-RgansQ",
                    download=False
                    # download=True,
                )
                return ydl.prepare_filename(info)

        downloaded_file = await asyncio.to_thread(run_download)

        await update.message.reply_text(
            f"Download succeeded:\n{downloaded_file}"
        )

    except Exception as e:
        logger.exception("YouTube test failed")
        await update.message.reply_text(
            f"YouTube test failed:\n{type(e).__name__}: {e}"
        )

# async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Send a message when the /help command is issued."""
#     keyboard = [
#       [
#           InlineKeyboardButton("Confirm", callback_data="confirm_yes"),
#           InlineKeyboardButton("Cancel", callback_data="confirm_no"),
#       ],
#       [InlineKeyboardButton("Help", callback_data="help_menu")],
#     ]

#   # Create the markup object
#     reply_markup = InlineKeyboardMarkup(keyboard)
#     await update.message.reply_text("Help!",reply_markup=reply_markup)

async def handle_longform_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        video_link = update.message.text
        video_formats = await asyncio.to_thread(get_youtube_download_info, video_link)
        user = update.effective_user
        file_name = f"{uuid.uuid4()}_{user.id}"
        # {file_name} {video_link} 
        if 'cache' not in context.user_data:
            context.user_data['cache']={}
        
        context.user_data['cache'][user.id]={"outtmpl":file_name,"url":video_link}
        
        keyboard_markup = [
        [
            InlineKeyboardButton("Best Audio", callback_data="bestaudio[ext=m4a]/bestaudio"),
            InlineKeyboardButton("Best Video", callback_data="bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4"),
        ],
        *[
            [
                InlineKeyboardButton(
                    text=f"{f['format']} {f['ext']} - {f['file_size']}", 
                    callback_data=f['format_id']
                )
            ] 
            for f in video_formats
        ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard_markup)
            
        await update.message.reply_text(text='select',reply_markup=reply_markup)
    except Exception:
        logger.exception("Error handling YouTube link")
        # await update.message.reply_text("Something went wrong while processing the YouTube link.")


async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        userMessage = update.message.text
        if not is_valid_link(userMessage):
            return
        if not is_accepted_social_link(userMessage):
            return
        # i want this to work for only non youtube short videos
        if is_a_longform_youtube_link(userMessage):
            await handle_longform_youtube_link(update, context)
            return
        user = update.effective_user
        fileName = f"{uuid.uuid4()}_{user.id}"

        # if(update.message.text
        downloadedFile = await asyncio.to_thread(download, update.message.text, fileName)
        # update.message.text
        # await update.message.reply_text(update.effective_user)
        # want to know the downloaded file fomrat so i can unlink it after sending it to the user
        # The file format will be determined by the downloader

        try:
            await update.message.reply_video(downloadedFile)
        finally:
            if Path(downloadedFile).exists():
                Path(downloadedFile).unlink()
        # .reply_text(update.effective_user)
    except Exception:
        logger.exception("Error handling message")
        # await update.message.reply_text("Something went wrong while processing your link.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    try:
        # 1. Acknowledge the button click immediately (stops the loading spinner)
        await query.answer()
        # await query.edit_message_text('downloading...')
        user = query.from_user
        format = query.data
        await query.delete_message()
        
        if 'cache' not in context.user_data or user.id not in context.user_data['cache']:
            await context.bot.send_message(chat_id=query.message.chat_id, text="Button expired (bot restarted). Please send the link again.")
            return
        if 'cache' in context.user_data:
            url = context.user_data['cache'][user.id]['url']
            outtmpl = context.user_data['cache'][user.id]['outtmpl']
            downloaded_file = await asyncio.to_thread(youtube_download,url=url,outtmpl=outtmpl,format=format)
            
            try:
                # with open(downloaded_file, 'rb') as video_file:
                if not format.startswith('bestaudio'):
                    print(f"Sending video: {downloaded_file}")
                    with open(downloaded_file, "rb") as video_file:
                        await context.bot.send_video(
                            chat_id=query.message.chat_id,
                            video=video_file
                        )      
                else:
                    print(f"Sending audio: {downloaded_file}")
                    with open(downloaded_file, "rb") as audio_file:
                        await context.bot.send_audio(
                            chat_id=query.message.chat_id,
                            audio=audio_file
                        )
            finally:
                if Path(downloaded_file).exists():
                    Path(downloaded_file).unlink()
        
        # 2. Check which button was clicked using callback_data
    #   if query.data == "confirm_yes":
    #   elif query.data == "confirm_no":
    #     await query.edit_message_text(text="Action cancelled.")
    #   elif query.data == "help_menu":
    #     await query.edit_message_text(text="Here is the help menu...")
    except Exception:
        logger.exception("Error handling callback")
        try:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                # text="Something went wrong while downloading the file."
            )
        except Exception:
            logger.exception("Could not send callback error message")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception", exc_info=context.error)


def build_application() -> Application:
    """Build the bot application with all handlers. Same for polling and webhook."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("ping", ping_command))
    # application.add_handler(CommandHandler("help", help_command))

    # on non command i.e message - echo the message on Telegram
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_error_handler(error_handler)

    return application


def main() -> None:
    mode = os.getenv("BOT_MODE", "polling").lower().strip()

    if mode == "webhook":
        import uvicorn

        port = int(os.getenv("PORT", "8000"))
        uvicorn.run("reel_liseer.webapp:app", host="0.0.0.0", port=port)
    else:
        build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()