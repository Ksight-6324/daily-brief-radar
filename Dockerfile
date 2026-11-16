FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

ARG REQUIREMENTS_FILE=requirements.txt
COPY requirements*.txt ./
RUN pip install -r ${REQUIREMENTS_FILE}

COPY . .

# Run as an unprivileged user. /data holds the database and dedupe history, /backups the rotated copies.
RUN useradd --create-home --uid 10001 brief \
    && mkdir -p /data /backups \
    && chown -R brief:brief /app /data /backups
USER brief

ENV BRIEF_MODE=subscription \
    BRIEF_DB_PATH=/data/brief.db \
    BRIEF_SEEN_FILE=/data/seen_items.json \
    BRIEF_BACKUP_DIR=/backups

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
