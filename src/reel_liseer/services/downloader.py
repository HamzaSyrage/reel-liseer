
import yt_dlp
from reel_liseer import config
ydl_opts = {}
ydl_opts['paths'] = {'home': config.DOWLOAD_PATH}
ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4/bestvideo+bestaudio/best/bestvideo+bestaudio'
# ydl_opts['outtmpl'] = "tessssst"

def download(url,outtmpl):
    ydl_opts['outtmpl'] = outtmpl
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        # print(info_dict)
        file_extension = info_dict.get('ext', None)
        ydl.download([url])


    return f"{config.DOWLOAD_PATH}/{outtmpl}.{file_extension}"

def get_youtube_download_info(url,outtmpl):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
    return info_dict

def youtube_download(url,outtmpl,format):
    ydl_opts['outtmpl'] = outtmpl
    ydl_opts['format'] = format
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        print(info_dict)
        file_extension = info_dict.get('ext', None)
        ydl.download([url])

    return f"{config.DOWLOAD_PATH}/{outtmpl}.{file_extension}"