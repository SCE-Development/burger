import enum
import functools
import prometheus_client


class ExitStatus(enum.Enum):
    SUCCESS = (0, "success")
    GENERIC_ERROR = (1, "generic_error")
    INVALID_ARGUMENT = (2, "invalid_argument")
    OUT_OF_MEMORY = (137, "out_of_memory")
    SEGMENTATION_FAULT = (139, "segmentation_fault")
    UNKNOWN_ERROR = (None, "unknown_error")

    def __init__(self, code, label):
        self.code = code
        self.label = label

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
        ['exit_status'] # ExitStatus.SUCCESS.label, ExitStatus.GENERIC_ERROR.label, etc
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
    _instance = None

    def __init__(self):
        raise RuntimeError('Call MetricsHandler.instance() instead')

    def init(self) -> None:
        for metric in Metrics:
            setattr(
                self,
                metric.title,
                metric.prometheus_type(
                    metric.title, metric.description, labelnames=metric.labels
                ),
            )

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls.__new__(cls)
            cls.init(cls)
        return cls._instance


    @staticmethod
    def track_subprocess_exit_code(func) -> None:

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            exit_code = func(*args, **kwargs)
            print(f"exit code: {exit_code}")
            if exit_code in [status.code for status in ExitStatus]:
                MetricsHandler.subprocess_count.labels(exit_status=ExitStatus(exit_code).label
                                                       ).inc(amount=1)
            else:
                MetricsHandler.subprocess_count.labels(exit_status=ExitStatus.UNKNOWN_ERROR.label
                                                       ).inc(amount=1)
            return exit_code
        return wrapper
