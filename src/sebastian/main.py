"""ASGI entrypoint: `uvicorn sebastian.main:app`."""

from .app import create_app

app = create_app()
