import argparse

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--videopath",
        help="path for video cache to be stored",
        default="./"
    )
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
    parser.add_argument(
        "--rtmp-stream-url",
        required=True,
        help="the location to stream downloaded files to, i.e. rtmp://localhost/stream/live"
    )
    parser.add_argument(
        "--cache-state-file",
        help="JSON file to persist cache state on server shutdown and recover on startup. if specified, the server will not empty the cache on shutdown"
    )
    return parser.parse_args()
