"""Environment helpers for server-managed Chromium subprocesses."""

from __future__ import annotations

import os
from pathlib import Path


def browser_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    configured = os.getenv("RESEARCH_HUB_CHROMIUM_LIB_DIR")
    lib_dir = Path(
        configured
        or "~/.cache/research-hub/chromium-libs/root/usr/lib/x86_64-linux-gnu"
    ).expanduser()
    if lib_dir.is_dir():
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{lib_dir}{':' + existing if existing else ''}"
    return env