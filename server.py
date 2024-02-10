import enum
import os
import re
import subprocess
import threading
from urllib.parse import unquote
import uvicorn

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pytube import YouTube, Playlist
import pytube.exceptions 
import prometheus_client

from modules.args import get_args
from modules.cache import Cache
from modules.metrics import MetricsHandler

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

def create_ffmpeg_stream(video_path:str, video_type:State, loop=False):
    command = [ 'ffmpeg', '-re', '-i', video_path, '-vf', f'scale=640:360', 
                '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', 
                '-c:a', 'aac', '-ar', '44100', '-f', 'flv', args.rtmp_stream_url ]
    # Loop the interlude stream
    if loop: 
        command[2:2] = ['-stream_loop', '-1']
    process_dict[video_type] = subprocess.Popen(
        command, 
        stdout=subprocess.DEVNULL, 
        stdin=subprocess.DEVNULL, 
        stderr=subprocess.DEVNULL
    )
    
def handle_interlude():
    while True:
        interlude_lock.acquire()
        create_ffmpeg_stream(args.interlude, State.INTERLUDE, True)

def handle_play(url:str):
    video = YouTube(url)
    current_video_dict["title"] = video.title
    current_video_dict["thumbnail"] = video.thumbnail_url 
    # Update process state
    if State.INTERLUDE in process_dict:
        # Stop interlude
        process_dict[State.INTERLUDE].terminate()
        process_dict.pop(State.INTERLUDE)
    download_and_play_video(url)
    process_dict[State.PLAYING].wait()
    # Start streaming video
    process_dict.pop(State.PLAYING)
    # Once video is finished playing (or stopped early), restart interlude
    if args.interlude:
        interlude_lock.release()

def download_next_video_in_list(playlist, current_index):
    next_index = current_index + 1 
    if next_index < len(playlist):
        video_url = playlist[next_index]
        if video_cache.find(Cache.get_video_id(video_url)) is None:
            video_cache.add(video_url)

def download_and_play_video(url):
    video_path = video_cache.find(Cache.get_video_id(url))
    if video_path is None:
        video_cache.add(url)
        video_path = video_cache.find(Cache.get_video_id(url))
    create_ffmpeg_stream(video_path, State.PLAYING)

def handle_playlist(playlist_url:str):
    playlist = Playlist(playlist_url)
    # Update process state
    if State.INTERLUDE in process_dict:
        interlude_process = process_dict.pop(State.INTERLUDE)
    # Stop interlude
    interlude_process.terminate()
    for i in range(len(playlist)):
        video_url = playlist[i]
        video = YouTube(video_url)
        # Only play age-unrestricted videos to avoid exceptions
        if not video.age_restricted:
            current_video_dict["title"] = video.title
            current_video_dict["thumbnail"] = video.thumbnail_url 
            # Start downloading next video
            threading.Thread(target=download_next_video_in_list, args=(playlist, i),).start()
            download_and_play_video(video_url)
            process_dict[State.PLAYING].wait()
    if args.interlude:
        interlude_lock.release()

def _get_url_type(url:str):
    try:
        pytube.Playlist(url)
        return UrlType.PLAYLIST
    except:
        try:
            pytube.YouTube(url)
            return UrlType.VIDEO
        except:
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
async def play(url: str):
    url = unquote(url)
    # Check if video is already playing
    if State.PLAYING in process_dict:
        raise HTTPException(status_code=409, detail="Please wait for the current video to end, then make the request")
    
    # Start thread to download video, stream it, and provide a response
    try:
        # Check if the given url is a valid video or playlist
        if _get_url_type(url) == UrlType.VIDEO:
            threading.Thread(target=handle_play, args=(url,)).start()
        elif _get_url_type(url) == UrlType.PLAYLIST:
            if len(Playlist(url)) == 0:
                raise Exception("This playlist url is invalid. Playlist may be empty or no longer exists.")
            threading.Thread(target=handle_playlist, args=(url,)).start()
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
        # logg
        raise HTTPException(status_code=500, detail=repr(e))
    
@app.post("/stop")
async def stop():
    # Check if there is a video playing to stop
    if State.PLAYING in process_dict:
        # Stop the video playing subprocess
        process_dict[State.PLAYING].terminate()
        process_dict.pop(State.PLAYING)

@app.get('/metrics')
def get_metrics():
    return Response(
        media_type="text/plain",
        content=prometheus_client.generate_latest(),
    )
    
@app.on_event("shutdown")
def signal_handler():
    for video_type in State:
        if video_type in process_dict:
            # Stop the video playing subprocess
            process_dict[video_type].terminate()
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
