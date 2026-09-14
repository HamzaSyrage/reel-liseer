from datetime import datetime
from pathlib import Path
import time
import uuid

from reel_liseer.services.downloader import download, youtube_download
from reel_liseer.config import DOWLOAD_PATH
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes,MessageHandler

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
    user = update.effective_user
    await update.message.reply_html(
        rf"Hi {user.mention_html()}!",
        reply_markup=ForceReply(selective=True),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    await update.message.reply_text("Help!")

async def handle_longform_youtube_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # i want to show the user a thumbnail with the title and durantion and under the message a 2 main btn best audio / best vide (with the size on each btn)
    # under them a list of all avaliabel res with the size on each btn
    # after he chosese we downlaod and replay adn delete the thumbnail message
    # pass for now  
    pass


async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    userMessage = update.message.text
    if not is_valid_link(userMessage):
        return
    if not is_accepted_social_link(userMessage):
        return
    # i want this to work for only non youtube short videos
    if is_a_longform_youtube_link(userMessage):
        await handle_longform_youtube_link(update, context)
        return
    user =update.effective_user
    fileName = f"{uuid.uuid4()}_{user.id}"
    print(fileName)
    # if(update.message.text
    downloadedFile=download(update.message.text,fileName)
    # update.message.text
    # await update.message.reply_text(update.effective_user)
    # want to know the downloaded file fomrat so i can unlink it after sending it to the user
    # The file format will be determined by the downloader

    await update.message.reply_video(downloadedFile)
    Path(downloadedFile).unlink()
    # .reply_text(update.effective_user)


def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # on non command i.e message - echo the message on Telegram
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()