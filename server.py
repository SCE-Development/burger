import enum
import os
import json
import subprocess
import threading
from urllib.parse import unquote
import uvicorn
import signal
import logging
import ssl

ssl._create_default_https_context = ssl._create_stdlib_context

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pytube import YouTube, Playlist
import pytube.exceptions
import prometheus_client
import psutil

from modules.args import get_args
from modules.cache import Cache
from modules.metrics import MetricsHandler


# Enum for the state of the video being processed
class State(enum.Enum):
    INTERLUDE = "interlude"
    PLAYING = "playing"


# Enum for the type of URL being processed
class UrlType(enum.Enum):
    VIDEO = "video"
    PLAYLIST = "playlist"
    UNKNOWN = "unknown"


# Create FastAPI instance
app = FastAPI()

# This dictionary is used to store the process IDs of running subprocesses, keyed by the type of video being processed (interlude or playing).
process_dict = {}

# This dictionary is used to store the title and thumbnail of the currently playing video.
current_video_dict = {}

interlude_lock = threading.Lock()

args = get_args()

# Create a cache object to store video files, initializing it with the file path specified in the command-line arguments or configuration settings. This instance is used to cache downloaded videos.
video_cache = Cache(file_path=args.videopath)

# Create a MetricsHandler instance to handle metrics for the server. This instance is used to handle metrics for the server.
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
def create_ffmpeg_stream(video_path: str, video_type: State, loop=False):
    # Create a subprocess to stream the video using FFmpeg
    command = [
        "ffmpeg",
        "-re",
        "-i",
        video_path,
        "-vf",
        f"scale=640:360",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-f",
        "flv",
        args.rtmp_stream_url,
    ]
    # Loop the interlude stream
    if loop:
        command[2:2] = ["-stream_loop", "-1"]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process_dict[video_type] = process.pid
    MetricsHandler.streams_count.labels(video_type=video_type.value).inc(amount=1)
    # the below function returns 0 if the video ended on its own
    # 137, 1
    exit_code = process.wait()
    MetricsHandler.subprocess_count.labels(
        exit_code=exit_code,
    ).inc()
    process_dict.pop(video_type)
    return exit_code


# stop the video by type
def stop_video_by_type(video_type: UrlType):
    if video_type in process_dict:
        kill_child_processes(process_dict[video_type])
        process_dict.pop(video_type)


# terminate a parent process and all its child processes using a specified signal.
def kill_child_processes(parent_pid, sig=signal.SIGKILL):
    try:
        parent = psutil.Process(parent_pid)
        parent.send_signal(sig)
        children = parent.children(recursive=True)
        for process in children:
            process.send_signal(sig)
    except psutil.NoSuchProcess:
        return


# Start a thread to handle the interlude stream
def handle_interlude():
    while True:
        # Wait for the lock to be released
        interlude_lock.acquire()

        # Check if the interlude stream is already running
        create_ffmpeg_stream(args.interlude, State.INTERLUDE, True)


def handle_play(url: str, loop: bool):
    video = YouTube(url)
    current_video_dict["title"] = video.title
    current_video_dict["thumbnail"] = video.thumbnail_url
    # Update process state
    if State.INTERLUDE in process_dict:
        # Stop interlude
        stop_video_by_type(State.INTERLUDE)
    download_and_play_video(url, loop)
    # Start streaming video
    # Once video is finished playing (or stopped early), restart interlude
    if args.interlude:
        interlude_lock.release()


def download_next_video_in_list(playlist, current_index):
    next_index = current_index + 1
    if next_index == (len(playlist)):
        next_index = 0
    video_url = playlist[next_index]
    if video_cache.find(Cache.get_video_id(video_url)) is None:
        video_cache.add(video_url)


# return the result of create_ffmpeg_stream
def download_and_play_video(url, loop):
    video_path = video_cache.find(Cache.get_video_id(url))
    if video_path is None:
        video_cache.add(url)
        video_path = video_cache.find(Cache.get_video_id(url))
    return create_ffmpeg_stream(video_path, State.PLAYING, loop)


def handle_playlist(playlist_url: str, loop: bool):
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
                threading.Thread(
                    target=download_next_video_in_list,
                    args=(playlist, i),
                ).start()
                result = download_and_play_video(video_url, False)
            if result != 0:
                break
        if not loop or result != 0:
            break
    if args.interlude:
        interlude_lock.release()


def _get_url_type(url: str):
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


def handle_cache_play():
    # Get all the videos in the cache
    cache_videos = video_cache.video_id_to_path

    # Loop through each video in the cache
    for _, video in cache_videos.items():

        # Store the current playing video information
        current_video_dict["title"] = video.title
        current_video_dict["thumbnail"] = video.thumbnail

        # Get the file path of the video to stream
        file_path = video.file_path
        response = create_ffmpeg_stream(file_path, State.PLAYING)

        # if the video ended on its own, continue to the next video, otherwise break out of the loop
        if response != 0:
            break


@app.get("/state")
async def state():
    result = {"state": State.INTERLUDE}
    if State.PLAYING in process_dict:
        result = {"state": State.PLAYING, "nowPlaying": current_video_dict}
    return result


@app.post("/play/file")
async def play_file(file_path: str = "cache", title: str = "", thumbnail: str = ""):

    # Store the current playing video information if any
    if title != "" and thumbnail != "":
        current_video_dict["title"] = title
        current_video_dict["thumbnail"] = thumbnail

    # If any video playing, stop it
    for video_type in State:
        # Stop the video playing subprocess
        stop_video_by_type(video_type)

    # Start thread to stream the video and provide a response
    try:

        # check if we are going to play all videos or a single video in the cache
        if file_path == "cache":

            # Start a thread to play all videos in the cache
            threading.Thread(target=handle_cache_play).start()

        else:
            # Start a thread to play a single video in the cache
            threading.Thread(
                target=create_ffmpeg_stream, args=(file_path, State.PLAYING)
            ).start()

        return {"detail": "Success"}

    except Exception as e:
        logging.exception(e)
        raise HTTPException(status_code=500, detail="check logs")
    finally:
        # Start streaming video
        # Once video is finished playing (or stopped early), restart interlude
        if args.interlude:
            interlude_lock.release()


@app.post("/play")
async def play(url: str, loop: bool = False):
    # Decode URL
    url = unquote(url)

    # Check if video is already playing
    if State.PLAYING in process_dict:
        raise HTTPException(
            status_code=409,
            detail="Please wait for the current video to end, then make the request",
        )

    # Start thread to download video, stream it, and provide a response
    try:

        # Get the type of URL (VIDEO, PLAYLIST, UNKNOWN)
        url_type = _get_url_type(url)

        # Check the type of URL and start the appropriate thread
        if url_type == UrlType.VIDEO:
            threading.Thread(target=handle_play, args=(url, loop)).start()

        elif url_type == UrlType.PLAYLIST:
            if len(Playlist(url)) == 0:
                raise Exception(
                    "This playlist url is invalid. Playlist may be empty or no longer exists."
                )
            threading.Thread(target=handle_playlist, args=(url, loop)).start()

        else:
            raise HTTPException(status_code=400, detail="given url is of unknown type")
        # Update Metrics
        MetricsHandler.video_count.inc()
        return {"detail": "Success"}

    # If download is unsuccessful, give response and reason
    except pytube.exceptions.AgeRestrictedError:
        raise HTTPException(status_code=400, detail="This video is age restricted :(")
    except pytube.exceptions.RegexMatchError:
        raise HTTPException(
            status_code=400, detail="That's not a YouTube link buddy ..."
        )
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


@app.get("/list")
async def getVideos():
    returnedResponse = []
    for key, value in video_cache.video_id_to_path.items():
        returnedResponse.append(
            {
                "id": key,
                "name": value.title,
                "path": value.file_path,
                "thumbnail": value.thumbnail,
            }
        )
    return json.dumps(returnedResponse)


@app.get("/metrics")
def get_metrics():
    return Response(
        media_type="text/plain",
        content=prometheus_client.generate_latest(),
    )


@app.get("/cache")
def get_cache():
    return FileResponse("static/cache.html")


@app.get("/debug")
def debug():
    return {
        "state": {
            "process_dict": process_dict,
            "current_video_dict": current_video_dict,
        },
        "cache": vars(video_cache),
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
