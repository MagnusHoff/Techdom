# syntax=docker/dockerfile:1
# Placeholder Dockerfile for the background worker service.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt || true

COPY . .

CMD ["python", "-m", "services.worker"]
