FROM python:3.11-slim
WORKDIR /app
# ffmpeg lets yt-dlp merge video and audio for the URL-list source
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY pipeline ./pipeline
RUN pip install --no-cache-dir .
COPY config ./config
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "--factory", "pipeline.api.app:app_factory", "--host", "0.0.0.0", "--port", "8000"]
