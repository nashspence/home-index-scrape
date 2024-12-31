FROM python:slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    attr \
    exiftool \
    ffmpeg \
    file \
    git \
    libgomp1 \
    libmediainfo0v5 \
    mediainfo \
    poppler-utils \
    tzdata \
    && apt-get clean

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY packages/home_index_scrape .
ENTRYPOINT ["python3", "/app/main.py"]
