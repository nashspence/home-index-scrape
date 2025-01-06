FROM python:alpine

ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apk add --no-cache \
    attr \
    exiftool \
    ffmpeg \
    file \
    git \
    libgomp \
    mediainfo \
    poppler-utils \
    tzdata

# Set the working directory
WORKDIR /app

# Copy requirements file and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source code
COPY packages/home_index_scrape .

# Set the entrypoint for the container
ENTRYPOINT ["python3", "/app/main.py"]
