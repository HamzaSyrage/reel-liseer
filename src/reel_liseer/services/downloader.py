import json
import os
import yt_dlp
from reel_liseer import config
import imageio_ffmpeg

ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

ydl_opts = {}
ydl_opts['paths'] = {'home': config.DOWLOAD_PATH}
ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4/bestvideo+bestaudio/best/bestvideo+bestaudio'
# ydl_opts['outtmpl'] = "tessssst"

ydl_opts['extractor_args'] = {
    'youtube': {
        'player_client': ['tv']
    }
}
ydl_opts["ffmpeg_location"] = ffmpeg_path

ydl_opts['cookiefile'] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'youtube-cookies.txt'
)

def download(url,outtmpl):
    l_ydl_opts= ydl_opts.copy()
    l_ydl_opts['outtmpl'] = f"{outtmpl}.%(ext)s"
    
    with yt_dlp.YoutubeDL(l_ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        # print(info_dict)
        file_extension = info_dict.get('ext', None)
        ydl.download([url])


    return f"{config.DOWLOAD_PATH}/{outtmpl}.{file_extension}"

def format_size(b: int) -> str:
    orig, i = b, 0
    while b >= 1024 and i < 5:
        b /= 1024
        i += 1
    u = ["Bytes", "KB", "MB", "GB", "TB", "PB"][i]
    return f"{b:.2f} {u}" if i else f"{orig} Bytes"

def get_youtube_download_info(url):
    l_ydl_opts = ydl_opts.copy()
    l_ydl_opts.pop('format', None)  
    with yt_dlp.YoutubeDL(l_ydl_opts) as ydl:
        meta = ydl.extract_info(
            url, download=False) 
        formats = meta.get('formats', [meta])
    toReturn = []
    seen_resolutions = set()

    for f in formats:
        # print("~~~~~~~~~~~~")
        # print(f"Format ID: {f.get('format_id')}")
        # print(f"Extension: {f.get('ext')}")
        # print(f"Format Name: {f.get('format')}")
        size_bytes = f.get("filesize") or f.get("filesize_approx")

        if size_bytes:
            fileSize = format_size(size_bytes)
            # print(f"File Size: {fileSize}")
            if f.get('ext') == 'mp4' and f.get('height') not in seen_resolutions:
                seen_resolutions.add(f.get('height'))
                toReturn.append({"ext":f.get('ext'),"format_id":f.get('format_id'),'format':f.get('format').split(" ")[2],"file_size":fileSize})

    return toReturn 


def youtube_download(url,outtmpl,format):
    l_ydl_opts = ydl_opts.copy()
    l_ydl_opts['outtmpl'] = f"{outtmpl}.%(ext)s"
    l_ydl_opts['format'] = format
    with yt_dlp.YoutubeDL(l_ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        downloaded_file = ydl.prepare_filename(info_dict)

    if format.startswith('bestaudio'):
        downloaded_file = f"{downloaded_file.rsplit('.', 1)[0]}.m4a"

    return downloaded_file