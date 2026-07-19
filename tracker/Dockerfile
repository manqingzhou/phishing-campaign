# Slim Python base. The healthcheck uses Python's stdlib (urllib) rather than
# curl, so no extra OS packages are required.
FROM python:3.12-slim

# Don't buffer stdout/stderr; no .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/clicks.db

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
# Use a fast, reachable PyPI mirror for the build.
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

# App source.
COPY . .

# Non-root user; owns the data dir (named volume mount point).
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
