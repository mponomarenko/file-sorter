FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    file libimage-exiftool-perl ffmpeg rmlint jdupes ca-certificates \
    build-essential cargo time \
    tesseract-ocr poppler-utils antiword unrtf \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

WORKDIR /app
COPY app /app/app
COPY cli /app/cli
COPY prompts /app/prompts
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && mkdir -p /work

# Create non-root user
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash -m appuser && \
    chown -R appuser:appuser /app /work

USER appuser
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/app/entrypoint.sh"]
