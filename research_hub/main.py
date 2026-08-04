"""Compatibility ASGI entrypoint.

Some tests and deployment templates import `research_hub.main:app`; keep that
stable while the implementation lives in `research_hub.app`.
"""

from .app import app, create_app

__all__ = ["app", "create_app"]
