import os
import threading
import enum
import subprocess
import argparse
import uvicorn

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pytube import YouTube
import pytube.exceptions 


class State(enum.Enum):
    INTERLUDE = "interlude"
    PLAYING = "playing"

app = FastAPI()
process_dict = {}
current_video_dict = {}
interlude_lock = threading.Lock()

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
    interlude_exists = get_args().interlude is None or not os.path.exists(get_args().interlude)
    while True:
        interlude_lock.acquire()
        # Check if interlude video file exists:
        if interlude_exists:
            process_dict[State.INTERLUDE] = None
        else:
            create_ffmpeg_stream(get_args().interlude, State.INTERLUDE, True)

def handle_play(url:str):
    interlude_process = process_dict.pop(State.INTERLUDE)
    # Download video
    video = YouTube(url)
    video = video.streams.filter(
                    resolution="360p",
                    progressive=True,
                  ).order_by("resolution").desc().first()
    video_to_play = video.default_filename
    # Download video is not already downloaded
    if (video_to_play not in os.listdir("./videos")):
        video.download("./videos/")
    # Stop interlude
    if interlude_process is not None:
        interlude_process.terminate() 
    # Start streaming video
    create_ffmpeg_stream(f'./videos/{video_to_play}', State.PLAYING)
    process_dict[State.PLAYING].wait()
    process_dict.pop(State.PLAYING)
    # Once video is finished playing (or stopped early), restart interlude
    interlude_lock.release()

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--interlude",
        help="file path to interlude file",
    )
    parser.add_argument(
        "--host",
        help="ip address to listen for requests on, i.e. 0.0.0.0",
        default='0.0.0.0',
    )
    parser.add_argument(
        "--port",
        type=int,
        help="port for the server to listen on, defaults to 5001",
        default=5001
    )
    return parser.parse_args()


# Ensure video folder exists
if not os.path.exists("./videos"):
   os.makedirs("./videos")
# Start up interlude by default
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
    try:
        video = YouTube(url)
        # Check for age restriction/video availability
        video.bypass_age_gate()
        video.check_availability()
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
    
@app.post("/stop")
async def stop():
    # Check if there is a video playing to stop
    if State.PLAYING in process_dict:
        # Stop the video playing subprocess
        process_dict[State.PLAYING].terminate()
    

if __name__ == "__main__":
    args = get_args()
    # 1. in this if statement, start the interlude thread only if
    # interlude file is specified
    # 2. remove the interlude check logic in start_ffmpeg_stream
    # 3. create a new branch, make a commit thats titled nicely, push and open a pr

    # 4. dm evan when done, i will teach you logging (if you time)
    uvicorn.run(app, host=args.host, port=args.port)
