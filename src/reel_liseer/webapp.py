import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from telegram import Update

from reel_liseer.admin import router as admin_router
from reel_liseer.main import build_application, register_commands
from reel_liseer.services import cookies

load_dotenv()

logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "telegram").strip().strip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip() or None

ptb_app = build_application()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application")
    cookies.seed_from_env()

    try:
        await ptb_app.initialize()
        await ptb_app.start()
        await register_commands(ptb_app)

        if WEBHOOK_URL:
            await ptb_app.bot.set_webhook(
                url=f"{WEBHOOK_URL}/{WEBHOOK_PATH}",
                allowed_updates=Update.ALL_TYPES,
                secret_token=WEBHOOK_SECRET,
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
app.include_router(admin_router)


@app.post(f"/{WEBHOOK_PATH}")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.update_queue.put(update)
    return {"ok": True}


@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "ok", "mode": "webhook"}


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}
