from collections import OrderedDict
from dataclasses import dataclass
import logging
import os
import uuid
import time

from pytube import YouTube

from modules.metrics import MetricsHandler
from urllib.parse import urlparse, parse_qs


@dataclass
class VideoInfo():
    file_path: str
    thumbnail: str
    title: str
    size_bytes: int

    def __str__(self):
        return f"VideoInfo(video_id={self.video_id}, file_path={self.file_path}, size_bytes={self.size_bytes})"


class Cache():
    def __init__(self, file_path: str, max_size_bytes: int = 2_000_000_000) -> None:
        self.file_path = file_path
        self.max_size_bytes = max_size_bytes
        self.current_size_bytes = 0
        self.video_id_to_path = OrderedDict()

    def add(self, url: str):
        video = YouTube(url)
        # Download video of set resolution
        video = video.streams.filter(
            resolution="360p",
            progressive=True,
        ).order_by("resolution").desc().first()
        if video.filesize > self.max_size_bytes:
            logging.info(
                f"Video size ({video.filesize} bytes) exceeds max cache size ({self.max_size_bytes} bytes). Caching cancelled.")
            return None
        video_file_name = video.default_filename
        start_time = time.time()
        video.download(self.file_path)
        video_id = self.get_video_id(url)
        video_file_name = str(uuid.uuid4()) + ".mp4"
        video_file_path = os.path.join(self.file_path, video_file_name)
        os.rename(
            os.path.join(self.file_path, video.default_filename),
            video_file_path,
        )
        logging.info(f"downloaded {url} to path {video_file_path}")
        end_time = time.time()
        MetricsHandler.download_time.inc(end_time - start_time)
        MetricsHandler.download_bits.inc(video.filesize)
        MetricsHandler.download_count.inc()

        video_info = VideoInfo(
            file_path=video_file_path,
            thumbnail=YouTube(url).thumbnail_url,
            title=YouTube(url).title,
            size_bytes=video.filesize
        )
        if self.current_size_bytes + video_info.size_bytes > self.max_size_bytes:
            target_bytes = self.max_size_bytes - video_info.size_bytes
            self._downsize_cache_to_target_bytes(target_bytes)
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
            f"current size {self.current_size_bytes}, downsizing to {target_bytes}")
        while self.current_size_bytes > target_bytes:
            removed_video_info = self.video_id_to_path.popitem(last=False)[1]
            self.current_size_bytes -= removed_video_info.size_bytes
            os.remove(removed_video_info.file_path)

    def clear(self):
        self._downsize_cache_to_target_bytes(0)

    @staticmethod
    def get_video_id(url) -> str:
        parsed_url = urlparse(url)
        video_id = parse_qs(parsed_url.query)['v'][0]
        return (video_id)
