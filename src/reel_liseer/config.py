import os

DOWNLOAD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.downloaded-media')

MAX_FILE_SIZE_MB = int(os.getenv('MAX_FILE_SIZE_MB', '50'))

MAX_CONCURRENT_JOBS = int(os.getenv('MAX_CONCURRENT_JOBS', '1'))

FFMPEG_THREADS = int(os.getenv('FFMPEG_THREADS', '2'))

FFMPEG_PRESET = os.getenv('FFMPEG_PRESET', 'veryfast')
FFMPEG_CRF = int(os.getenv('FFMPEG_CRF', '26'))

MEDIA_WRITE_TIMEOUT = float(os.getenv('MEDIA_WRITE_TIMEOUT', '300'))
