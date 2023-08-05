import os
import threading
import enum
import subprocess
import uvicorn
import re

from args import get_args

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pytube import YouTube, Playlist
import pytube.exceptions 

from cache import Cache


class State(enum.Enum):
    INTERLUDE = "interlude"
    PLAYING = "playing"

class UrlType(enum.Enum):
    VIDEO = "video"
    PLAYLIST = "playlist"

app = FastAPI()
process_dict = {}
current_video_dict = {}
interlude_lock = threading.Lock()
args = get_args()
video_cache = Cache(file_path=args.videopath)

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
                '-c:a', 'aac', '-ar', '44100', '-f', 'flv', 'rtmp://localhost:1935/live/mystream' ]
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
    # Update process state
    interlude_process = process_dict.pop(State.INTERLUDE)
    # Add video to cache
    video_cache.add(url)
    # Stop interlude
    interlude_process.terminate()
    # Start streaming video
    # create_ffmpeg_stream(video_cache.find(Cache.get_video_id(url)), State.PLAYING)
    download_and_play_video(url, State.PLAYING)
    process_dict[State.PLAYING].wait()
    process_dict.pop(State.PLAYING)
    # Once video is finished playing (or stopped early), restart interlude
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
    interlude_process = process_dict.pop(State.INTERLUDE)
    # Stop interlude
    interlude_process.terminate()
    for i in range(len(playlist)):
        video_url = playlist[i]
        video = YouTube(video_url)
        current_video_dict["title"] = video.title
        current_video_dict["thumbnail"] = video.thumbnail_url 
        threading.Thread(target=download_next_video_in_list, args=(playlist, i),).start()
        download_and_play_video(video_url)
        process_dict[State.PLAYING].wait()
    interlude_lock.release()

def _get_url_type(url:str):
    split_url = re.split(r'/|\?', url)
    if split_url[3] == "playlist":
        return UrlType.PLAYLIST
    else:
        return UrlType.VIDEO


# Ensure video folder exists
if not os.path.exists(args.videopath):
   os.makedirs(args.videopath)
# Start up interlude by default
if args.interlude:
    threading.Thread(target=handle_interlude).start()

@app.get("/")
async def root():
    return { "message": "Hello World" }

@app.get("/state")
async def state():
    if State.INTERLUDE in process_dict:
        return { "state": State.INTERLUDE }
    else:
        return  {
                    "state": State.PLAYING,
                    "nowPlaying": current_video_dict
                }

@app.post("/play")
async def play(url: str):
    # Check if video is already playing
    if State.PLAYING in process_dict:
        raise HTTPException(status_code=409, detail="please wait for the current video to end, then make the request")
        
    # Start thread to download video, stream it, and provide a response
    if _get_url_type(url) == UrlType.VIDEO:
        try:
            video = YouTube(url)
            current_video_dict["title"] = video.title
            current_video_dict["thumbnail"] = video.thumbnail_url 
            threading.Thread(target=handle_play, args=(url,)).start()
            return { "detail": "Success" }
        # If download is unsuccessful, give response and reason
        except pytube.exceptions.AgeRestrictedError:
            raise HTTPException(status_code=400, detail="This video is age restricted :(")
        except pytube.exceptions.VideoUnavailable:
            raise HTTPException(status_code=404, detail="This video is unavailable :(")
        except pytube.exceptions.RegexMatchError:
            raise HTTPException(status_code=400, detail="That's not a YouTube link buddy ...")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        try:
            threading.Thread(target=handle_playlist, args=(url,)).start()
            return { "detail": "Success" }
        except Exception as e:
            print(e)

    
@app.post("/stop")
async def stop():
    # Check if there is a video playing to stop
    if State.PLAYING in process_dict:
        # Stop the video playing subprocess
        process_dict[State.PLAYING].terminate()
    
@app.on_event("shutdown")
def signal_handler():
    video_cache.clear()

if __name__ == "__main__":
    
    uvicorn.run(app, host=args.host, port=args.port)