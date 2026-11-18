from dotenv import load_dotenv

load_dotenv()

import os

DOWNLOAD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.downloaded-media')

MAX_CONCURRENT_JOBS = int(os.getenv('MAX_CONCURRENT_JOBS', '1'))

FFMPEG_THREADS = int(os.getenv('FFMPEG_THREADS', '2'))

FFMPEG_PRESET = os.getenv('FFMPEG_PRESET', 'veryfast')
FFMPEG_CRF = int(os.getenv('FFMPEG_CRF', '26'))

MEDIA_WRITE_TIMEOUT = float(os.getenv('MEDIA_WRITE_TIMEOUT', '0')) or None

TELEGRAM_API_BASE_URL = os.getenv('TELEGRAM_API_BASE_URL', '').strip().rstrip('/') or None


COOKIES_DIR = os.getenv('COOKIES_DIR', '').strip() or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '.cookies'
)

ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '').strip() or 'admin'
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '').strip() or os.getenv('ADMIN_TOKEN', '').strip()

COOKIE_DOMAINS = tuple(
    d.strip().lower().lstrip('.')
    for d in os.getenv(
        'COOKIE_DOMAINS',
        'youtube.com,google.com,instagram.com,facebook.com,tiktok.com,twitter.com,x.com,reddit.com',
    ).split(',')
    if d.strip()
)

COOKIES_MAX_BYTES = int(os.getenv('COOKIES_MAX_BYTES', str(2 * 1024 * 1024)))
