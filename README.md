# burger

SCE TV

## Running locally

### With Docker

- [ ] run the server with:

```
docker-compose -f docker-compose.dev.yml up --build
```

### Without Docker

make sure you have docker and python installed!

- [ ] create a virtual environment with

```
mkdir venv
python -m venv ./venv
source ./venv/bin/activate
```

- [ ] install requirements locally with

```
pip install -r requirements.txt
```

- [ ] also install latest working version of pytube with:

```
pip install git+https://github.com/joejztang/pytube.git@499268313ada0b971dc5b6718986b27d97731f05
```

- [ ] start the streaming server with

```
docker-compose -f docker-compose.dev.yml up -d streaming-server
```

- [ ] run the server with:

```
python server.py --rtmp-stream-url rtmp://localhost:1935/live/mystream
```

## Playing a video

- [ ] visit the webpage at http://localhost:5001/ and add a url from youtube
- [ ] open the rtmp stream url `rtmp://localhost/live/mystream` in VLC with
      <img width="591" alt="image" src="https://github.com/SCE-Development/sce-tv/assets/36345325/58238640-f26a-4d7c-87b3-bdf645e30a22">
- [ ] ensure the stream runs in VLC
