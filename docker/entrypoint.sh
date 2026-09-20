#!/bin/sh
# Run migrations, then the server. Host/port come from the ewo config file
# (mounted at $EWO_CONFIG_FILENAME) — no uvicorn parameters here.
set -e
alembic upgrade head
exec python -m ewo.server
