FROM python:3.12-slim

# git is needed by the report publisher
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# dependencies first for layer caching; uvicorn is runtime-only and
# deliberately NOT in requirements.txt / pyproject
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "uvicorn[standard]"

COPY pyproject.toml alembic.ini ./
COPY ewo/ ewo/
RUN pip install --no-cache-dir --no-deps . \
    && python -m compileall -q ewo

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 9999
ENTRYPOINT ["/entrypoint.sh"]
