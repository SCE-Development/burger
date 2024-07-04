from collections import OrderedDict
from dataclasses import dataclass
import logging
import os
import uuid
import json

from pytube import YouTube

from modules.metrics import MetricsHandler
from urllib.parse import urlparse, parse_qs


@dataclass
class VideoInfo:
    file_path: str
    thumbnail: str
    title: str
    size_bytes: int

    def __str__(self):
        return f"VideoInfo(video_id={self.video_id}, file_path={self.file_path}, size_bytes={self.size_bytes})"


class Cache:
    def __init__(
        self,
        file_path: str,
        cache_file: str = None,
        max_size_bytes: int = 2_000_000_000,
    ) -> None:
        self.file_path = file_path
        self.max_size_bytes = max_size_bytes
        self.current_size_bytes = 0
        self.cache_file = cache_file
        self.video_id_to_path = OrderedDict()

    def add(self, url: str):
        video = YouTube(url)
        # Download video of set resolution
        video = (
            video.streams.filter(
                resolution="360p",
                progressive=True,
            )
            .order_by("resolution")
            .desc()
            .first()
        )
        if video.filesize > self.max_size_bytes:
            logging.info(
                f"Video size ({video.filesize} bytes) exceeds max cache size ({self.max_size_bytes} bytes). Caching cancelled."
            )
            return None
        if self.current_size_bytes + video.filesize > self.max_size_bytes:
            target_bytes = self.max_size_bytes - video.filesize
            self._downsize_cache_to_target_bytes(target_bytes)
            MetricsHandler.cache_size.set(len(self.video_id_to_path))
            MetricsHandler.cache_size_bytes.set(self.current_size_bytes)
        video_file_name = video.default_filename
        with MetricsHandler.download_time.time():
            video.download(self.file_path)
        MetricsHandler.data_downloaded.inc(video.filesize)
        MetricsHandler.video_download_count.inc()
        video_id = self.get_video_id(url)
        video_file_name = str(uuid.uuid4()) + ".mp4"
        video_file_path = os.path.join(self.file_path, video_file_name)
        os.rename(
            os.path.join(self.file_path, video.default_filename),
            video_file_path,
        )
        logging.info(f"downloaded {url} to path {video_file_path}")
        video_info = VideoInfo(
            file_path=video_file_path,
            thumbnail=YouTube(url).thumbnail_url,
            title=YouTube(url).title,
            size_bytes=video.filesize,
        )
        self.video_id_to_path[video_id] = video_info
        self.current_size_bytes += video_info.size_bytes
        MetricsHandler.cache_size.set(len(self.video_id_to_path))
        MetricsHandler.cache_size_bytes.set(self.current_size_bytes)

    def find(self, video_id: str):
        if video_id in self.video_id_to_path:
            self.video_id_to_path.move_to_end(video_id)
            MetricsHandler.cache_hit_count.inc()
            return self.video_id_to_path[video_id].file_path
        MetricsHandler.cache_miss_count.inc()
        return None

    def _downsize_cache_to_target_bytes(self, target_bytes: int):
        logging.info(
            f"current size {self.current_size_bytes}, downsizing to {target_bytes}"
        )
        while self.current_size_bytes > target_bytes:
            removed_video_info = self.video_id_to_path.popitem(last=False)[1]
            self.current_size_bytes -= removed_video_info.size_bytes
            os.remove(removed_video_info.file_path)

    def clear(self):
        self._downsize_cache_to_target_bytes(0)

    def populate_cache(self):
        try:
            # open the file and read the data
            with open(self.cache_file, "r") as f:

                # json.load converts the json data into python dictionary
                dict_data = json.load(f)

            # populate the cache
            for video_key, video_info in dict_data.items():
                if not os.path.exists(video_info["file_path"]):
                    logging.info(f"{video_info['file_path']} was not found on disk")
                    continue
                self.video_id_to_path[video_key] = VideoInfo(
                    file_path=video_info["file_path"],
                    thumbnail=video_info["thumbnail"],
                    title=video_info["title"],
                    size_bytes=video_info["size_bytes"],
                )
                self.current_size_bytes += video_info["size_bytes"]
                MetricsHandler.cache_size.set(len(self.video_id_to_path))
                MetricsHandler.cache_size_bytes.set(self.current_size_bytes)
            logging.info(
                f"Read {len(self.video_id_to_path)} items from cache file {self.cache_file}"
            )
        except Exception:
            logging.exception(f"unable to read cache data from {self.cache_file}")

    def write_cache(self):
        try:
            # cache state
            cache_state = {}
            for video_id, video_info in self.video_id_to_path.items():
                cache_state[video_id] = {
                    "file_path": video_info.file_path,
                    "thumbnail": video_info.thumbnail,
                    "title": video_info.title,
                    "size_bytes": video_info.size_bytes,
                }

            # serializing json
            json_data = json.dumps(cache_state, indent=4)

            # open the file and write the data
            with open(self.cache_file, "w") as f:
                f.write(json_data)

            logging.info(
                f"Wrote {len(cache_state)} items to cache file {self.cache_file}"
            )
        except Exception:
            logging.exception(f"unable to write cache data to {self.cache_file}")

    @staticmethod
    def get_video_id(url) -> str:
        parsed_url = urlparse(url)
        video_id = parse_qs(parsed_url.query)["v"][0]
        return video_id
