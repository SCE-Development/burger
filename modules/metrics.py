import enum
import prometheus_client


class Metrics(enum.Enum):
    VIDEO_COUNT = (
        "video_count",
        "Number of videos played",
        prometheus_client.Counter,
    )

    STREAMS_COUNT = (
        "streams_count",
        "Number of streams created",
        prometheus_client.Counter,
        ['video_type'] # playing, interlude
    )

    SUBPROCESS_COUNT = (
        "subprocess_count",
        "Number of subprocesses ended",
        prometheus_client.Counter,
        ['exit_code'] # 0, 137, 1 etc
    )

    CACHE_SIZE = (
        "cache_size",
        "Total entries in cache",
        prometheus_client.Gauge,
    )

    CACHE_SIZE_BYTES = (
        "cache_size_bytes",
        "Current cache size in bytes",
        prometheus_client.Gauge,
    )

    CACHE_HIT_COUNT = (
        "cache_hit_count",
        "Number of successful cache retrievals",
        prometheus_client.Counter,
    )

    CACHE_MISS_COUNT = (
        "cache_miss_count",
        "Number of failed cache retrievals",
        prometheus_client.Counter,

    HTTP_REQUEST_COUNT = (
        "http_request_count",
        "Number of requests received for each endpoint",
        prometheus_client.Counter,
        ['endpoint'],
    )

    def __init__(self, title, description, prometheus_type, labels=()):
        # we use the above default value for labels because it matches what's used
        # in the prometheus_client library's metrics constructor, see
        # https://github.com/prometheus/client_python/blob/fd4da6cde36a1c278070cf18b4b9f72956774b05/prometheus_client/metrics.py#L115
        self.title = title
        self.description = description
        self.prometheus_type = prometheus_type
        self.labels = labels


class MetricsHandler:
    @classmethod
    def init(self) -> None:
        for metric in Metrics:
            setattr(
                self,
                metric.title,
                metric.prometheus_type(
                    metric.title, metric.description, labelnames=metric.labels
                ),
            )
