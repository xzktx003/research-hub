"""Run the Research Hub API with uvicorn."""

from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "research_hub.app:app",
        host=os.environ.get("RESEARCH_HUB_HOST", "127.0.0.1"),
        port=int(os.environ.get("RESEARCH_HUB_PORT", "8080")),
        reload=os.environ.get("RESEARCH_HUB_RELOAD", "0") == "1",
    )
