import enum
import os
import re
import subprocess
import threading
from urllib.parse import unquote
import uvicorn
import signal
import logging

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pytube import YouTube, Playlist
import pytube.exceptions 
import prometheus_client
import psutil

from modules.args import get_args
from modules.cache import Cache
from modules.metrics import MetricsHandler, ExitStatus

class State(enum.Enum):
    INTERLUDE = "interlude"
    PLAYING = "playing"

class UrlType(enum.Enum):
    VIDEO = "video"
    PLAYLIST = "playlist"
    UNKNOWN = "unknown"

app = FastAPI()
process_dict = {}
current_video_dict = {}
interlude_lock = threading.Lock()
args = get_args()
video_cache = Cache(file_path=args.videopath)
metrics_handler = MetricsHandler.instance()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# return the result of process.wait()
def create_ffmpeg_stream(video_path:str, video_type:State, loop=False):
    command = [ 'ffmpeg', '-re', '-i', video_path, '-vf', f'scale=640:360', 
                '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', 
                '-c:a', 'aac', '-ar', '44100', '-f', 'flv', args.rtmp_stream_url ]
    # Loop the interlude stream
    if loop: 
        command[2:2] = ['-stream_loop', '-1']
    process = subprocess.Popen(
        command, 
        stdout=subprocess.DEVNULL, 
        stdin=subprocess.DEVNULL, 
        stderr=subprocess.DEVNULL
    )
    process_dict[video_type] = process.pid
    MetricsHandler.streams_count.labels(video_type=video_type.value).inc(amount=1)

    # the below function returns 0 if the video ended on its own
    # 137, 1
    exit_code = process.wait()
    MetricsHandler.subprocess_count.labels(
        exit_status=ExitStatus.from_exit_code(exit_code).label,
    ).inc()
    return exit_code

def stop_video_by_type(video_type: UrlType):
  if video_type in process_dict:
      kill_child_processes(process_dict[video_type])
      process_dict.pop(video_type)
  pass

def kill_child_processes(parent_pid, sig=signal.SIGKILL):
    try:
        parent = psutil.Process(parent_pid)
        parent.send_signal(sig)
        children = parent.children(recursive=True)
        for process in children:
            process.send_signal(sig)
    except psutil.NoSuchProcess:
        return
    
def handle_interlude():
    while True:
        interlude_lock.acquire()
        create_ffmpeg_stream(args.interlude, State.INTERLUDE, True)

def handle_play(url:str,loop:bool):
    video = YouTube(url)
    current_video_dict["title"] = video.title
    current_video_dict["thumbnail"] = video.thumbnail_url 
    # Update process state
    if State.INTERLUDE in process_dict:
        # Stop interlude
        stop_video_by_type(State.INTERLUDE)
    download_and_play_video(url,loop)
    # Start streaming video
    # Once video is finished playing (or stopped early), restart interlude
    if args.interlude:
        interlude_lock.release()

def download_next_video_in_list(playlist, current_index):
    next_index = current_index + 1 
    if next_index==(len(playlist)):
        next_index = 0
    video_url = playlist[next_index]
    if video_cache.find(Cache.get_video_id(video_url)) is None:
        video_cache.add(video_url)

# return the result of create_ffmpeg_stream
def download_and_play_video(url,loop):
    video_path = video_cache.find(Cache.get_video_id(url))
    if video_path is None:
        video_cache.add(url)
        video_path = video_cache.find(Cache.get_video_id(url))
    return create_ffmpeg_stream(video_path, State.PLAYING,loop)

def handle_playlist(playlist_url:str, loop:bool):
    playlist = Playlist(playlist_url)
    # Update process state
    if State.INTERLUDE in process_dict:
        stop_video_by_type(State.INTERLUDE)
    # Stop interlude
    while True:
        for i in range(len(playlist)):
            video_url = playlist[i]
            video = YouTube(video_url)
            # Only play age-unrestricted videos to avoid exceptions
            if not video.age_restricted:
                current_video_dict["title"] = video.title
                current_video_dict["thumbnail"] = video.thumbnail_url 
                # Start downloading next video
                threading.Thread(target=download_next_video_in_list, args=(playlist, i),).start()
                result = download_and_play_video(video_url,False)
            if result !=0:
                break
        if not loop or result!=0:
            break
    if args.interlude:
        interlude_lock.release()

def _get_url_type(url:str):
    try:
        playlist = pytube.Playlist(url)
        logging.info(f"{url} is a playlist with {len(playlist)} videos")
        return UrlType.PLAYLIST
    except:
        try:
            pytube.YouTube(url)
            return UrlType.VIDEO
        except:
            logging.error(f"url {url} is not a playlist or video!")
            return UrlType.UNKNOWN


@app.get("/state")
async def state():
    if State.PLAYING in process_dict:
        return {
                    "state": State.PLAYING,
                    "nowPlaying": current_video_dict
                }
    return  { "state": State.INTERLUDE }

@app.post("/play")
async def play(url: str,loop: bool=False):
    url = unquote(url)
    # Check if video is already playing
    if State.PLAYING in process_dict:
        raise HTTPException(status_code=409, detail="Please wait for the current video to end, then make the request")
    
    # Start thread to download video, stream it, and provide a response
    try:
        url_type = _get_url_type(url)
        # Check if the given url is a valid video or playlist
        if url_type == UrlType.VIDEO:
            threading.Thread(target=handle_play, args=(url,loop)).start()
        elif url_type == UrlType.PLAYLIST:
            if len(Playlist(url)) == 0:
                raise Exception("This playlist url is invalid. Playlist may be empty or no longer exists.")
            threading.Thread(target=handle_playlist, args=(url,loop)).start()
        else:
            raise HTTPException(status_code=400, detail="given url is of unknown type")
        # Update Metrics
        MetricsHandler.video_count.inc(amount=1)
        return { "detail": "Success" }

    # If download is unsuccessful, give response and reason
    except pytube.exceptions.AgeRestrictedError:
        raise HTTPException(status_code=400, detail="This video is age restricted :(")
    except pytube.exceptions.RegexMatchError:
        raise HTTPException(status_code=400, detail="That's not a YouTube link buddy ...")
    except pytube.exceptions.VideoUnavailable:
        raise HTTPException(status_code=404, detail="This video is unavailable :(")
    except Exception as e:
        logging.exception(e)
        raise HTTPException(status_code=500, detail="check logs")
    
@app.post("/stop")
async def stop():
    current_video_dict.clear()
    # Check if there is a video playing to stop
    if State.PLAYING in process_dict:
        # Stop the video playing subprocess
        stop_video_by_type(State.PLAYING)

@app.get('/metrics')
def get_metrics():
    return Response(
        media_type="text/plain",
        content=prometheus_client.generate_latest(),
    )

@app.get('/debug')
def debug():
    return {
            "state": {
                "process_dict": process_dict,
                "current_video_dict": current_video_dict
                    },
            "cache": vars(video_cache)
            }
    
@app.on_event("shutdown")
def signal_handler():
    for video_type in State:
        if video_type in process_dict:
            # Stop the video playing subprocess
            stop_video_by_type(video_type)
    video_cache.clear()

app.mount("/", StaticFiles(directory="static", html=True), name="static")

# we have a separate __name__ check here due to how FastAPI starts
# a server. the file is first ran (where __name__ == "__main__")
# and then calls `uvicorn.run`. the call to run() reruns the file,
# this time __name__ == "server". the separate __name__ if statement
# is so a thread starts up the interlude after the server is ready to go
if __name__ == "server":
    # Start up interlude by default
    if args.interlude:
        threading.Thread(target=handle_interlude).start()
    # Ensure video folder exists
    if not os.path.exists(args.videopath):
        os.makedirs(args.videopath)

if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=True,
    )
