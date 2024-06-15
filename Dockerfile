FROM python:3.10.2-slim-buster

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1

ENV PYTHONUNBUFFERED 1

RUN apt-get update && apt-get install -y ffmpeg git gcc

COPY requirements.txt /app

# Fix Pytube regex errors :)
RUN pip3 install git+https://github.com/joejztang/pytube.git@499268313ada0b971dc5b6718986b27d97731f05

RUN pip3 install -r /app/requirements.txt

EXPOSE 5001

STOPSIGNAL SIGINT

ENTRYPOINT ["python","server.py"]
