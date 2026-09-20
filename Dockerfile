FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY pipeline ./pipeline
RUN pip install --no-cache-dir .
COPY config ./config
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "--factory", "pipeline.api.app:app_factory", "--host", "0.0.0.0", "--port", "8000"]
