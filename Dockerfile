FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip

COPY pyproject.toml /app/pyproject.toml
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY docs /app/docs
COPY prompts /app/prompts
COPY templates /app/templates
COPY src /app/src

RUN python -m pip install --no-cache-dir . \
    && python -m pip install --no-cache-dir "uvicorn>=0.30,<1.0"

RUN mkdir -p /app/data /app/logs /app/storage

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "auto_writing.app:app", "--host", "0.0.0.0", "--port", "8000"]
