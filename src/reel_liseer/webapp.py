import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from telegram import Update

from reel_liseer.main import build_application

load_dotenv()

logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "telegram").strip().strip("/")

ptb_app = build_application()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application")

    try:
        await ptb_app.initialize()
        await ptb_app.start()

        if WEBHOOK_URL:
            url = f"{WEBHOOK_URL}/{WEBHOOK_PATH}"
            await ptb_app.bot.set_webhook(
                url=url,
                allowed_updates=Update.ALL_TYPES,
            )

        logger.info("Application started")

        yield

    finally:
        logger.info("Application shutdown started")

        try:
            await ptb_app.stop()
        except Exception:
            logger.exception("Failed to stop Telegram application")

        try:
            await ptb_app.shutdown()
        except Exception:
            logger.exception("Failed to shutdown Telegram application")

        logger.info("Application shutdown completed")


app = FastAPI(lifespan=lifespan)


@app.post(f"/{WEBHOOK_PATH}")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.update_queue.put(update)
    return {"ok": True}


@app.get("/")
def root():
    return {"status": "ok", "mode": "webhook"}


@app.get("/health")
def health():
    return {"status": "ok"}


# @app.get("/youtube-cookies.txt")
# def get_youtube_cookies():
#     cookie_file_path = os.path.join(
#         os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
#         "youtube-cookies.txt",
#     )
#     return FileResponse(
#         path=cookie_file_path,
#         media_type="text/plain",
#     )


# @app.get("/txt")
# def get_txt_cookies():
#     cookie_file_path = "/app/src/youtube-cookies.txt"
#     return FileResponse(
#         path=cookie_file_path,
#         media_type="text/plain",
#     )


# @app.get("/printing")
# def get_printing():
#     return {
#         "status": "ok",
#         "path": printing(),
#     }