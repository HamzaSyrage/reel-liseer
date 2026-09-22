# reel-liseer

A Telegram bot I made because I got tired of opening links people send in Telegram groups. Now you can add the bot to your group and it will download the videos and send them back to the group for you.

This bot is privacy-first, so it doesn't save any user data or keep track of who uses it.

You can add the bot to any group and use it without worrying about it storing information about the people using it.

## Try it

[**@Reel_Liseer_bot**](https://t.me/Reel_Liseer_bot)

## Setup

### Clone

```bash
git clone https://github.com/HamzaSyrage/reel-liseer.git
cd reel-liseer
```

### Using uv

Install [uv](https://docs.astral.sh/uv/) if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install dependencies:

```bash
uv sync
```

Run:

```bash
uv run python src/reel_liseer/main.py
```

### Using Python + venv + pip

Generate `requirements.txt` from the lock file:

```bash
uv export --format requirements-txt --output-file requirements.txt
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run:

```bash
python src/reel_liseer/main.py
```

## Environment

Create a `.env` file:

```env
TELEGRAM_BOT_TOKEN=
# polling/webhook
BOT_MODE=polling
WEBHOOK_URL=
WEBHOOK_PATH=telegram
PORT=8000
```

Get `TELEGRAM_BOT_TOKEN` from [@BotFather](https://t.me/BotFather) using `/newbot`.


## Commands

| Command           | Description                 |
| ----------------- | --------------------------- |
| `/start`          | Start the bot               |
| `/ping`           | Check if the bot is running |
| `/options <URL>`  | Choose the download format  |

You can also send a video link directly.
