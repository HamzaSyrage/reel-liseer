import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from telegram import Update

from reel_liseer.main import build_application

load_dotenv()

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "telegram").strip().strip("/")

ptb_app = build_application()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # start the bot
    await ptb_app.initialize()
    await ptb_app.start()

    # register webhook with telegram (needs public HTTPS url)
    if WEBHOOK_URL:
        url = f"{WEBHOOK_URL}/{WEBHOOK_PATH}"
        await ptb_app.bot.set_webhook(url=url, allowed_updates=Update.ALL_TYPES)

    yield

    # cleanup on shutdown
    try:
        await ptb_app.bot.delete_webhook()
    except Exception:
        pass
    await ptb_app.stop()
    await ptb_app.shutdown()


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
