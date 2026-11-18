import html
import logging
import re
import shutil
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg
import yt_dlp

from reel_liseer import config
from reel_liseer.services import cookies

ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

ydl_opts = {}
ydl_opts['paths'] = {'home': config.DOWNLOAD_PATH}
ydl_opts['format'] = 'bestvideo+bestaudio/best'

ydl_opts["ffmpeg_location"] = ffmpeg_path

ydl_opts['merge_output_format'] = 'mp4'
ydl_opts['retries'] = 3
ydl_opts['fragment_retries'] = 3
# ydl_opts['postprocessors'] = [
#     {
#         'key': 'FFmpegMetadata',
#         'when': 'after_move'
#     }
# ]
ydl_opts['concurrent_fragment_downloads'] = 1

logger = logging.getLogger(__name__)


def js_runtimes() -> dict:
    found: dict = {}
    try:
        import deno

        found['deno'] = {'path': str(deno.find_deno_bin())}
    except Exception:  # noqa: BLE001 - optional dependency / binary missing
        if shutil.which('deno'):
            found['deno'] = {}
    for name in ('node', 'bun'):
        if shutil.which(name):
            found[name] = {}
    return found


JS_RUNTIMES = js_runtimes()


@contextmanager
def _opts(**overrides):
    opts = dict(ydl_opts)
    if JS_RUNTIMES:
        opts['js_runtimes'] = JS_RUNTIMES
    for key, value in overrides.items():
        if value is None:
            opts.pop(key, None)
        else:
            opts[key] = value
    with cookies.cookiefile() as cookie_path:
        if cookie_path:
            opts['cookiefile'] = cookie_path
        yield opts

OUTTMPL = '%(title).100s.%(ext)s'

BROWSER_UA = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/138.0 Safari/537.36'
)

REDDIT_POST_RE = re.compile(
    r'^https?://(?:www\.|old\.|new\.|m\.|np\.)?reddit\.com/'
    r'(?P<path>r/[^/?#]+/comments/[A-Za-z0-9]+)(?:/[^?#]*)?/?(?:[?#].*)?$',
    re.IGNORECASE,
)
_REDDIT_TITLE_RE = re.compile(r'id="embed-title".*?<h1[^>]*>(.*?)</h1>', re.DOTALL)
_REDDIT_VIDEO_ID_RE = re.compile(r'v\.redd\.it/([A-Za-z0-9]{8,})')

GOOD_VIDEO_CODECS = ('avc1', 'h264', 'hev1', 'hvc1')
KNOWN_BAD_VIDEO_CODECS = ('vp9', 'vp09', 'vp8', 'av01', 'theora', 'mp4v')


def _media_metadata(info: dict, url: str, path: str, title: str | None = None) -> dict:
    return {
        'path': path,
        'title': title or info.get('title') or 'media',
        'duration': info.get('duration'),
        'width': info.get('width'),
        'height': info.get('height'),
        'ext': info.get('ext'),
        'webpage_url': info.get('webpage_url') or url,
    }


def _codec_tier(fmt: dict) -> int | None:
    vcodec = fmt.get('vcodec')
    if vcodec == 'none':
        return None
    if vcodec is None:
        return 1
    vcodec = vcodec.lower()
    if any(bad in vcodec for bad in KNOWN_BAD_VIDEO_CODECS):
        return 3
    if any(good in vcodec for good in GOOD_VIDEO_CODECS):
        return 0
    return 1


def select_format(info: dict) -> str | None:
    formats = info.get('formats') or [info]

    videos = [f for f in formats if f.get('vcodec') != 'none']
    if not videos:
        return None

    videos.sort(key=lambda f: (
        _codec_tier(f),
        -(f.get('height') or 0),
        -(f.get('tbr') or 0),
    ))
    video = videos[0]

    if video.get('acodec') != 'none':
        return video['format_id']

    audios = [
        f for f in formats
        if f.get('vcodec') == 'none' and f.get('acodec') != 'none'
    ]
    if not audios:
        return video['format_id']

    audios.sort(key=lambda f: -(f.get('tbr') or 0))
    return f"{video['format_id']}+{audios[0]['format_id']}"


def _fetch_text(url: str, timeout: float = 15) -> str:
    request = urllib.request.Request(
        url,
        headers={'User-Agent': BROWSER_UA, 'Accept-Language': 'en-US,en;q=0.9'},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode('utf-8', 'replace')


def resolve_reddit(url: str) -> tuple[str | None, str | None]:
    match = REDDIT_POST_RE.match(url or '')
    if not match:
        return None, None

    path = match.group('path')
    title = None
    for page_url in (
        f"https://embed.reddit.com/{path}/",
        f"https://old.reddit.com/{path}/",
        f"https://www.reddit.com/{path}/",
    ):
        try:
            page = _fetch_text(page_url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("reddit page %s unavailable: %s", page_url, exc)
            continue

        if title is None:
            title_match = _REDDIT_TITLE_RE.search(page)
            if title_match:
                title = html.unescape(re.sub(r'<[^>]+>', '', title_match.group(1))).strip() or None

        video_id = _REDDIT_VIDEO_ID_RE.search(page)
        if video_id:
            return f"https://v.redd.it/{video_id.group(1)}/HLSPlaylist.m3u8", title

    return None, title


def download(url):
    direct_url, reddit_title = resolve_reddit(url)
    source = direct_url or url

    with _opts(format=None, merge_output_format=None) as metadata_opts, yt_dlp.YoutubeDL(metadata_opts) as ydl:
        info_dict = ydl.extract_info(source, download=False)

    if info_dict.get('_type') == 'playlist' and info_dict.get('entries'):
        info_dict = info_dict['entries'][0]

    format_spec = select_format(info_dict)
    if not format_spec:
        raise RuntimeError(f"no playable video format found: {url}")

    with _opts(format=format_spec, outtmpl=f"{uuid4().hex}_{OUTTMPL}") as opts, yt_dlp.YoutubeDL(opts) as ydl:
        info_dict = ydl.extract_info(source, download=True)
        downloaded_file = Path(ydl.prepare_filename(info_dict))

    if not downloaded_file.is_file() or downloaded_file.stat().st_size == 0:
        downloaded_file.unlink(missing_ok=True)
        raise RuntimeError(f"yt-dlp produced no file for {url}")

    return _media_metadata(info_dict, url, str(downloaded_file), reddit_title)

def format_size(b: int) -> str:
    orig, i = b, 0
    while b >= 1024 and i < 5:
        b /= 1024
        i += 1
    u = ["Bytes", "KB", "MB", "GB", "TB", "PB"][i]
    return f"{b:.2f} {u}" if i else f"{orig} Bytes"


def get_video_formats(url):
    direct_url, reddit_title = resolve_reddit(url)

    with _opts(format=None, merge_output_format=None) as l_ydl_opts, yt_dlp.YoutubeDL(l_ydl_opts) as ydl:
        meta = ydl.extract_info(direct_url or url, download=False)

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

    return reddit_title or meta.get('title', 'video'), result


def download_with_format(url, outtmpl, format):
    direct_url, reddit_title = resolve_reddit(url)

    overrides = {'outtmpl': f"{outtmpl}.%(ext)s", 'format': format}
    if not format.startswith('bestaudio'):
        overrides['merge_output_format'] = 'mp4'

    with _opts(**overrides) as l_ydl_opts, yt_dlp.YoutubeDL(l_ydl_opts) as ydl:
        info_dict = ydl.extract_info(direct_url or url, download=True)
        downloaded_file = ydl.prepare_filename(info_dict)

    if format.startswith('bestaudio'):
        downloaded_file = f"{downloaded_file.rsplit('.', 1)[0]}.m4a"

    return _media_metadata(info_dict, url, downloaded_file, reddit_title)



def probe_url(url: str) -> dict:
    direct_url, _ = resolve_reddit(url)
    with _opts(format=None, merge_output_format=None) as opts:
        opts['skip_download'] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(direct_url or url, download=False)
    if info.get('_type') == 'playlist' and info.get('entries'):
        info = info['entries'][0]
    return {'title': info.get('title'), 'extractor': info.get('extractor_key'),
            'formats': len(info.get('formats') or [])}
