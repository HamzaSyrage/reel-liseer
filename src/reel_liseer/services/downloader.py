from uuid import uuid4
import imageio_ffmpeg
import yt_dlp
from reel_liseer import config

ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

ydl_opts = {}
ydl_opts['paths'] = {'home': config.DOWNLOAD_PATH}
ydl_opts['format'] = (
    'best[ext=mp4][vcodec^=avc1][height<=1080]+bestaudio[ext=m4a]/'
    'best[ext=mp4][vcodec!*=vp]+bestaudio[ext=m4a]/'
    'best[ext=mp4]/'
    'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
    'best/bestvideo+bestaudio/best'
)

ydl_opts["ffmpeg_location"] = ffmpeg_path

# ydl_opts['cookiefile'] = os.path.join(
#     os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
#     'youtube-cookies.txt'
# )

# ydl_opts['cookiefile'] = "/app/src/youtube-cookies.txt"
# print("COOKIE FILE:", ydl_opts["cookiefile"])
# print("COOKIE EXISTS:", os.path.exists(ydl_opts["cookiefile"]))
# print("COOKIE SIZE:", os.path.getsize(ydl_opts["cookiefile"]) if os.path.exists(ydl_opts["cookiefile"]) else None)
# ydl_opts['extractor_args'] = {
#     'youtube': {
#         'player_client': ['default', 'web_embedded']
#     }
# }

ydl_opts['merge_output_format'] = 'mp4'
ydl_opts['retries'] = 3
ydl_opts['fragment_retries'] = 3
ydl_opts['max_filesize'] = config.MAX_FILE_SIZE_MB * 1024 * 1024
# ydl_opts['postprocessors'] = [
#     {
#         'key': 'FFmpegMetadata',
#         'when': 'after_move'
#     }
# ]
ydl_opts['concurrent_fragment_downloads'] = 1

OUTTMPL = '%(title).100s.%(ext)s'


def _media_metadata(info: dict, url: str, path: str) -> dict:
    return {
        'path': path,
        'title': info.get('title') or 'media',
        'duration': info.get('duration'),
        'width': info.get('width'),
        'height': info.get('height'),
        'ext': info.get('ext'),
        'webpage_url': info.get('webpage_url') or url,
    }


def download(url):
    l_ydl_opts = ydl_opts.copy()
    l_ydl_opts['outtmpl'] = f"{uuid4().hex}_{OUTTMPL}"

    with yt_dlp.YoutubeDL(l_ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        downloaded_file = ydl.prepare_filename(info_dict)

    return _media_metadata(info_dict, url, downloaded_file)

def format_size(b: int) -> str:
    orig, i = b, 0
    while b >= 1024 and i < 5:
        b /= 1024
        i += 1
    u = ["Bytes", "KB", "MB", "GB", "TB", "PB"][i]
    return f"{b:.2f} {u}" if i else f"{orig} Bytes"


def get_video_formats(url):
    l_ydl_opts = ydl_opts.copy()
    l_ydl_opts.pop('format', None)
    l_ydl_opts.pop('merge_output_format', None)

    with yt_dlp.YoutubeDL(l_ydl_opts) as ydl:
        meta = ydl.extract_info(url, download=False)

    formats = meta.get('formats', [])
    video_formats = {}

    for f in formats:
        if not f.get('vcodec') or f.get('vcodec') == 'none':
            continue

        height = f.get('height')
        if not height:
            continue

        size = f.get('filesize') or f.get('filesize_approx')

        if not size and f.get('tbr') and meta.get('duration'):
            size = f['tbr'] * 1000 / 8 * meta['duration']

        current = video_formats.get(height)

        score = (
            1 if f.get('ext') == 'mp4' else 0,
            1 if f.get('acodec') and f.get('acodec') != 'none' else 0,
            f.get('fps') or 0,
            f.get('tbr') or 0,
        )

        if current is None or score > current['_score']:
            video_formats[height] = {
                'format_id': f.get('format_id'),
                'height': height,
                'ext': f.get('ext'),
                'filesize': size,
                'acodec': f.get('acodec'),
                '_score': score,
            }

    result = []

    for height, f in sorted(video_formats.items(), reverse=True):
        result.append({
            'format_id': f['format_id'],
            'format': f'{height}p',
            'file_size': format_size(f['filesize']) if f['filesize'] else '',
            'ext': f['ext'],
            'height': height,
            'acodec': f['acodec'],
        })

    return meta.get('title', 'video'), result 


def download_with_format(url, outtmpl, format):
    l_ydl_opts = ydl_opts.copy()
    l_ydl_opts['outtmpl'] = f"{outtmpl}.%(ext)s"
    l_ydl_opts['format'] = format
    if not format.startswith('bestaudio'):
        l_ydl_opts['merge_output_format'] = 'mp4'

    with yt_dlp.YoutubeDL(l_ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        downloaded_file = ydl.prepare_filename(info_dict)

    if format.startswith('bestaudio'):
        downloaded_file = f"{downloaded_file.rsplit('.', 1)[0]}.m4a"

    return _media_metadata(info_dict, url, downloaded_file)
