FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    cmake \
    build-essential \
    libzbar0 \
    libopenblas-dev \
    liblapack-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект сохраняя структуру
COPY creator/ ./creator/
COPY scanner/ ./scanner/
COPY api/ ./api/

# Папки которые создаются в рантайме
RUN mkdir -p qr_codes qr_scans face_photos storage
