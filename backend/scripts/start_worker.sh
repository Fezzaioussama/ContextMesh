#!/bin/sh
set -eu

# The API service applies migrations; Compose starts the worker after it is healthy.
exec python -m app.worker
