"""Compatibility module for callers that import `research_hub.api`."""

from .app import app, create_app

__all__ = ["app", "create_app"]
