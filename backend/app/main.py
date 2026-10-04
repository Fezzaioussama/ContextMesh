"""Uvicorn factory entry point; bootstrap owns concrete application composition."""

from app.bootstrap.api import create_app

__all__ = ["create_app"]
