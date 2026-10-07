"""Uvicorn factory entry point; bootstrap owns concrete application composition."""

from app.setup.api import create_app

__all__ = ["create_app"]
